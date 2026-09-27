"""
Core domain models and zero-overhead data structures for the low-latency order matching engine.

Optimized for high-throughput execution with explicit __slots__ to prevent dynamic __dict__
allocations and reduce memory footprint on the hot path.
"""

from __future__ import annotations
from enum import IntEnum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from matching_engine import LimitLevel


class Side(IntEnum):
    """Order side: BUY (bid) or SELL (ask)."""
    BUY = 1
    SELL = 2

    def opposite(self) -> Side:
        """Return the counterparty side."""
        return Side.SELL if self == Side.BUY else Side.BUY

    def __str__(self) -> str:
        return "BUY" if self == Side.BUY else "SELL"


class OrderType(IntEnum):
    """Order type classification."""
    LIMIT = 1
    MARKET = 2
    CANCEL = 3

    def __str__(self) -> str:
        if self == OrderType.LIMIT:
            return "LIMIT"
        elif self == OrderType.MARKET:
            return "MARKET"
        return "CANCEL"


class Order:
    """
    Intrusive Doubly-Linked List Order Node.
    
    Uses __slots__ for maximum cache locality and zero dynamic dictionary overhead.
    Embedding 'prev', 'next', and 'level' pointers directly in the Order object eliminates
    the allocation of separate wrapper nodes when queueing orders in price levels.
    """
    __slots__ = (
        'order_id',
        'trader_id',
        'side',
        'price',
        'quantity',
        'initial_quantity',
        'timestamp',
        'order_type',
        'prev',
        'next',
        'level',
    )

    def __init__(
        self,
        order_id: int,
        trader_id: str,
        side: Side,
        price: float | int,
        quantity: int,
        timestamp: int,
        order_type: OrderType = OrderType.LIMIT,
    ) -> None:
        self.order_id: int = order_id
        self.trader_id: str = trader_id
        self.side: Side = side
        self.price: float = float(price)
        self.quantity: int = quantity
        self.initial_quantity: int = quantity
        self.timestamp: int = timestamp
        self.order_type: OrderType = order_type
        
        # Intrusive doubly-linked list pointers for O(1) queue manipulation
        self.prev: Optional[Order] = None
        self.next: Optional[Order] = None
        self.level: Optional[LimitLevel] = None

    @property
    def filled_quantity(self) -> int:
        return self.initial_quantity - self.quantity

    @property
    def is_filled(self) -> bool:
        return self.quantity == 0

    def __repr__(self) -> str:
        return (
            f"Order(id={self.order_id}, trader={self.trader_id}, side={self.side.name}, "
            f"price={self.price:.2f}, qty={self.quantity}/{self.initial_quantity}, ts={self.timestamp})"
        )


class Trade:
    """
    Represents an executed trade between a passive maker order and an aggressive taker order.
    """
    __slots__ = (
        'trade_id',
        'maker_order_id',
        'taker_order_id',
        'price',
        'quantity',
        'timestamp',
        'maker_side',
    )

    def __init__(
        self,
        trade_id: int,
        maker_order_id: int,
        taker_order_id: int,
        price: float | int,
        quantity: int,
        timestamp: int,
        maker_side: Side = Side.SELL,
    ) -> None:
        self.trade_id: int = trade_id
        self.maker_order_id: int = maker_order_id
        self.taker_order_id: int = taker_order_id
        self.price: float = float(price)
        self.quantity: int = quantity
        self.timestamp: int = timestamp
        self.maker_side: Side = maker_side

    def __repr__(self) -> str:
        return (
            f"Trade(id={self.trade_id}, maker={self.maker_order_id}, taker={self.taker_order_id}, "
            f"price={self.price:.2f}, qty={self.quantity}, ts={self.timestamp})"
        )


class BBO:
    """
    Best Bid and Offer (Top of Book) snapshot.
    """
    __slots__ = (
        'bid_price',
        'bid_volume',
        'ask_price',
        'ask_volume',
        'timestamp',
    )

    def __init__(
        self,
        bid_price: Optional[float],
        bid_volume: int,
        ask_price: Optional[float],
        ask_volume: int,
        timestamp: int,
    ) -> None:
        self.bid_price: Optional[float] = bid_price
        self.bid_volume: int = bid_volume
        self.ask_price: Optional[float] = ask_price
        self.ask_volume: int = ask_volume
        self.timestamp: int = timestamp

    @property
    def spread(self) -> Optional[float]:
        if self.bid_price is not None and self.ask_price is not None:
            return round(self.ask_price - self.bid_price, 6)
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.bid_price is not None and self.ask_price is not None:
            return round((self.ask_price + self.bid_price) / 2.0, 6)
        return None

    def __repr__(self) -> str:
        bid_str = f"{self.bid_volume}@{self.bid_price:.2f}" if self.bid_price is not None else "None"
        ask_str = f"{self.ask_volume}@{self.ask_price:.2f}" if self.ask_price is not None else "None"
        return f"BBO(Bid=[{bid_str}], Ask=[{ask_str}], Spread={self.spread})"
