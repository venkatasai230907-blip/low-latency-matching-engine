"""
High-Performance Deterministic Limit Order Book (LOB) Matching Engine.

Implements strict Price-Time Priority (FIFO) matching with:
- Doubly-linked list per price level for O(1) order insertion and cancellation.
- Sorted price indexing via bisect for O(1) top-of-book (BBO) inspection and O(log M) level insertion.
- Intrusive order pointer tracking for zero-allocation order removal.
- Real-time market data event hooks for deterministic replay and downstream listeners.
"""

from __future__ import annotations
import bisect
from typing import Optional, TYPE_CHECKING, Callable

from models import Side, OrderType, Order, Trade, BBO

if TYPE_CHECKING:
    from market_data import MarketDataGateway


class LimitLevel:
    """
    Represents a single price level in the limit order book.
    
    Contains a doubly-linked list of resting orders executing in strict FIFO order.
    """
    __slots__ = (
        'price',
        'total_volume',
        'order_count',
        'head',
        'tail',
    )

    def __init__(self, price: float | int) -> None:
        self.price: float = float(price)
        self.total_volume: int = 0
        self.order_count: int = 0
        self.head: Optional[Order] = None
        self.tail: Optional[Order] = None

    def append(self, order: Order) -> None:
        """Append an order to the tail of this price level (FIFO queue). O(1)"""
        order.level = self
        order.next = None
        if self.tail is None:
            # First order at this price level
            self.head = order
            self.tail = order
            order.prev = None
        else:
            order.prev = self.tail
            self.tail.next = order
            self.tail = order
        
        self.total_volume += order.quantity
        self.order_count += 1

    def remove(self, order: Order) -> None:
        """Remove an arbitrary order node from this price level. O(1)"""
        if order.prev is not None:
            order.prev.next = order.next
        else:
            self.head = order.next

        if order.next is not None:
            order.next.prev = order.prev
        else:
            self.tail = order.prev

        self.total_volume -= order.quantity
        self.order_count -= 1
        
        order.prev = None
        order.next = None
        order.level = None

    def reduce_volume(self, quantity: int) -> None:
        """Reduce aggregated volume at this price level without removing the node."""
        self.total_volume -= quantity

    @property
    def is_empty(self) -> bool:
        return self.order_count == 0 or self.head is None

    def __repr__(self) -> str:
        return f"LimitLevel(price={self.price:.2f}, vol={self.total_volume}, orders={self.order_count})"


class MatchingEngine:
    """
    Deterministic Low-Latency Limit Order Book Matching Engine.
    
    Guarantees:
    - Strict Price-Time Priority (FIFO)
    - Zero-allocation intrusive doubly-linked list for active price levels
    - O(1) best bid/ask retrieval
    - O(1) order lookup and cancellation by order_id
    """
    __slots__ = (
        '_bids',
        '_asks',
        '_bid_prices',
        '_ask_prices',
        '_orders',
        '_trade_id_counter',
        '_market_data_gateway',
    )

    def __init__(self, market_data_gateway: Optional[MarketDataGateway] = None) -> None:
        # Price level maps: price -> LimitLevel
        self._bids: dict[float, LimitLevel] = {}
        self._asks: dict[float, LimitLevel] = {}

        # Sorted price arrays:
        # _bid_prices: sorted ascending -> best bid is _bid_prices[-1] (O(1) peek)
        # _ask_prices: sorted ascending -> best ask is _ask_prices[0] (O(1) peek)
        self._bid_prices: list[float] = []
        self._ask_prices: list[float] = []

        # Order lookup table: order_id -> Order
        self._orders: dict[int, Order] = {}

        # Trade counter
        self._trade_id_counter: int = 0

        # Optional market data broadcast gateway
        self._market_data_gateway: Optional[MarketDataGateway] = market_data_gateway

    @property
    def market_data_gateway(self) -> Optional[MarketDataGateway]:
        return self._market_data_gateway

    @market_data_gateway.setter
    def market_data_gateway(self, gateway: MarketDataGateway) -> None:
        self._market_data_gateway = gateway

    def _next_trade_id(self) -> int:
        self._trade_id_counter += 1
        return self._trade_id_counter

    def get_order(self, order_id: int) -> Optional[Order]:
        """Look up active resting order by ID. O(1)"""
        return self._orders.get(order_id)

    @property
    def best_bid_price(self) -> Optional[float]:
        """Return the highest active bid price or None if book is empty. O(1)"""
        return self._bid_prices[-1] if self._bid_prices else None

    @property
    def best_ask_price(self) -> Optional[float]:
        """Return the lowest active ask price or None if book is empty. O(1)"""
        return self._ask_prices[0] if self._ask_prices else None

    def get_bbo(self, timestamp: int = 0) -> BBO:
        """Return current Best Bid and Offer (BBO) snapshot. O(1)"""
        best_bid = self.best_bid_price
        bid_vol = self._bids[best_bid].total_volume if best_bid is not None else 0
        best_ask = self.best_ask_price
        ask_vol = self._asks[best_ask].total_volume if best_ask is not None else 0
        return BBO(best_bid, bid_vol, best_ask, ask_vol, timestamp)

    def add_limit_order(self, order: Order) -> tuple[list[Trade], Optional[Order]]:
        """
        Process an incoming limit order.
        
        Matches aggressively against opposing book if price crosses the spread.
        Any residual unfilled quantity is inserted into the book with FIFO priority.
        
        Returns:
            (trades, resting_order): List of executed trades and the resting Order (or None if filled).
        """
        if order.quantity <= 0:
            return [], None

        trades: list[Trade] = []
        side = order.side

        if side == Side.BUY:
            # Match against asks while order price >= best ask price
            while order.quantity > 0 and self._ask_prices:
                best_ask = self._ask_prices[0]
                if order.price < best_ask:
                    break

                ask_level = self._asks[best_ask]
                maker = ask_level.head
                while maker is not None and order.quantity > 0:
                    match_qty = order.quantity if order.quantity < maker.quantity else maker.quantity
                    trade_price = maker.price  # Price improvement: execution occurs at resting maker's price
                    
                    trade = Trade(
                        trade_id=self._next_trade_id(),
                        maker_order_id=maker.order_id,
                        taker_order_id=order.order_id,
                        price=trade_price,
                        quantity=match_qty,
                        timestamp=order.timestamp,
                        maker_side=Side.SELL,
                    )
                    trades.append(trade)
                    
                    if self._market_data_gateway is not None:
                        self._market_data_gateway.on_trade_executed(trade)

                    order.quantity -= match_qty
                    maker.quantity -= match_qty
                    ask_level.reduce_volume(match_qty)

                    next_maker = maker.next
                    if maker.quantity == 0:
                        ask_level.remove(maker)
                        del self._orders[maker.order_id]
                    maker = next_maker

                if ask_level.is_empty:
                    del self._asks[best_ask]
                    self._ask_prices.pop(0)

            # Rest remaining quantity in bids
            if order.quantity > 0:
                price = order.price
                if price not in self._bids:
                    level = LimitLevel(price)
                    self._bids[price] = level
                    bisect.insort(self._bid_prices, price)
                else:
                    level = self._bids[price]

                level.append(order)
                self._orders[order.order_id] = order
                
                if self._market_data_gateway is not None:
                    self._market_data_gateway.on_order_placed(order)
                
                return trades, order

            return trades, None

        else:
            # Match against bids while order price <= best bid price
            while order.quantity > 0 and self._bid_prices:
                best_bid = self._bid_prices[-1]
                if order.price > best_bid:
                    break

                bid_level = self._bids[best_bid]
                maker = bid_level.head
                while maker is not None and order.quantity > 0:
                    match_qty = order.quantity if order.quantity < maker.quantity else maker.quantity
                    trade_price = maker.price  # Price improvement: execution occurs at resting maker's price
                    
                    trade = Trade(
                        trade_id=self._next_trade_id(),
                        maker_order_id=maker.order_id,
                        taker_order_id=order.order_id,
                        price=trade_price,
                        quantity=match_qty,
                        timestamp=order.timestamp,
                        maker_side=Side.BUY,
                    )
                    trades.append(trade)

                    if self._market_data_gateway is not None:
                        self._market_data_gateway.on_trade_executed(trade)

                    order.quantity -= match_qty
                    maker.quantity -= match_qty
                    bid_level.reduce_volume(match_qty)

                    next_maker = maker.next
                    if maker.quantity == 0:
                        bid_level.remove(maker)
                        del self._orders[maker.order_id]
                    maker = next_maker

                if bid_level.is_empty:
                    del self._bids[best_bid]
                    self._bid_prices.pop()

            # Rest remaining quantity in asks
            if order.quantity > 0:
                price = order.price
                if price not in self._asks:
                    level = LimitLevel(price)
                    self._asks[price] = level
                    bisect.insort(self._ask_prices, price)
                else:
                    level = self._asks[price]

                level.append(order)
                self._orders[order.order_id] = order

                if self._market_data_gateway is not None:
                    self._market_data_gateway.on_order_placed(order)

                return trades, order

            return trades, None

    def execute_market_order(
        self,
        side: Side,
        quantity: int,
        taker_order_id: int = 0,
        trader_id: str = "TAKER",
        timestamp: int = 0,
    ) -> tuple[list[Trade], int]:
        """
        Execute an aggressive market order against resting liquidity.
        
        Consumes available volume across price levels until filled or opposite book is exhausted.
        Does not rest unfilled balance.
        
        Returns:
            (trades, unfilled_quantity): List of executed trades and any unfilled residual volume.
        """
        if quantity <= 0:
            return [], 0

        trades: list[Trade] = []
        remaining_qty = quantity

        if side == Side.BUY:
            while remaining_qty > 0 and self._ask_prices:
                best_ask = self._ask_prices[0]
                ask_level = self._asks[best_ask]
                maker = ask_level.head
                
                while maker is not None and remaining_qty > 0:
                    match_qty = remaining_qty if remaining_qty < maker.quantity else maker.quantity
                    trade = Trade(
                        trade_id=self._next_trade_id(),
                        maker_order_id=maker.order_id,
                        taker_order_id=taker_order_id,
                        price=maker.price,
                        quantity=match_qty,
                        timestamp=timestamp,
                        maker_side=Side.SELL,
                    )
                    trades.append(trade)

                    if self._market_data_gateway is not None:
                        self._market_data_gateway.on_trade_executed(trade)

                    remaining_qty -= match_qty
                    maker.quantity -= match_qty
                    ask_level.reduce_volume(match_qty)

                    next_maker = maker.next
                    if maker.quantity == 0:
                        ask_level.remove(maker)
                        del self._orders[maker.order_id]
                    maker = next_maker

                if ask_level.is_empty:
                    del self._asks[best_ask]
                    self._ask_prices.pop(0)

        else:  # Side.SELL
            while remaining_qty > 0 and self._bid_prices:
                best_bid = self._bid_prices[-1]
                bid_level = self._bids[best_bid]
                maker = bid_level.head
                
                while maker is not None and remaining_qty > 0:
                    match_qty = remaining_qty if remaining_qty < maker.quantity else maker.quantity
                    trade = Trade(
                        trade_id=self._next_trade_id(),
                        maker_order_id=maker.order_id,
                        taker_order_id=taker_order_id,
                        price=maker.price,
                        quantity=match_qty,
                        timestamp=timestamp,
                        maker_side=Side.BUY,
                    )
                    trades.append(trade)

                    if self._market_data_gateway is not None:
                        self._market_data_gateway.on_trade_executed(trade)

                    remaining_qty -= match_qty
                    maker.quantity -= match_qty
                    bid_level.reduce_volume(match_qty)

                    next_maker = maker.next
                    if maker.quantity == 0:
                        bid_level.remove(maker)
                        del self._orders[maker.order_id]
                    maker = next_maker

                if bid_level.is_empty:
                    del self._bids[best_bid]
                    self._bid_prices.pop()

        return trades, remaining_qty

    def cancel_order(self, order_id: int, timestamp: int = 0) -> Optional[Order]:
        """
        Cancel a resting order by its ID in O(1) time complexity.
        
        Returns the cancelled Order or None if the order_id was not found.
        """
        order = self._orders.get(order_id)
        if order is None:
            return None

        level = order.level
        if level is not None:
            level.remove(order)
            if level.is_empty:
                price = level.price
                if order.side == Side.BUY:
                    del self._bids[price]
                    idx = bisect.bisect_left(self._bid_prices, price)
                    if idx < len(self._bid_prices) and self._bid_prices[idx] == price:
                        self._bid_prices.pop(idx)
                else:
                    del self._asks[price]
                    idx = bisect.bisect_left(self._ask_prices, price)
                    if idx < len(self._ask_prices) and self._ask_prices[idx] == price:
                        self._ask_prices.pop(idx)

        del self._orders[order_id]

        if self._market_data_gateway is not None:
            self._market_data_gateway.on_order_cancelled(order, timestamp=timestamp)

        return order

    def get_l2_snapshot(self, depth: int = 5) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
        """
        Retrieve top N price levels on Bid and Ask sides with aggregated volume.
        
        Returns:
            (bids, asks):
            bids: list of (price, volume) ordered from highest price to lowest
            asks: list of (price, volume) ordered from lowest price to highest
        """
        bids = [
            (p, self._bids[p].total_volume)
            for p in reversed(self._bid_prices[-depth:])
        ]
        asks = [
            (p, self._asks[p].total_volume)
            for p in self._ask_prices[:depth]
        ]
        return bids, asks

    @property
    def total_resting_volume(self) -> int:
        """Total aggregate quantity of all resting orders currently in the book."""
        return sum(level.total_volume for level in self._bids.values()) + \
               sum(level.total_volume for level in self._asks.values())

    @property
    def order_count(self) -> int:
        """Total count of active resting orders in the engine."""
        return len(self._orders)

    def clear(self) -> None:
        """Reset the matching engine state."""
        self._bids.clear()
        self._asks.clear()
        self._bid_prices.clear()
        self._ask_prices.clear()
        self._orders.clear()
        self._trade_id_counter = 0
