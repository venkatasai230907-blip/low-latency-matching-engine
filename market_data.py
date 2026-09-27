"""
Market Data Gateway, Event Journaling, and Pub/Sub Broadcaster.

Provides:
- Lightweight structured event logging for deterministic state audit and replay.
- Pub/Sub subscription mechanism for trade executions, BBO updates, and order events.
- Audit replay harness to reconstruct engine state from historical event journals.
"""

from __future__ import annotations
from typing import Callable, Optional
from models import Side, Order, Trade, BBO, OrderType


class MarketEvent:
    """Base class for all discrete market data and matching engine events."""
    __slots__ = ('timestamp',)

    def __init__(self, timestamp: int) -> None:
        self.timestamp: int = timestamp


class OrderPlacedEvent(MarketEvent):
    """Event emitted when a limit order rests in the book."""
    __slots__ = (
        'order_id',
        'trader_id',
        'side',
        'price',
        'quantity',
    )

    def __init__(
        self,
        order_id: int,
        trader_id: str,
        side: Side,
        price: float,
        quantity: int,
        timestamp: int,
    ) -> None:
        super().__init__(timestamp)
        self.order_id: int = order_id
        self.trader_id: str = trader_id
        self.side: Side = side
        self.price: float = price
        self.quantity: int = quantity

    def __repr__(self) -> str:
        return (
            f"OrderPlaced(id={self.order_id}, trader={self.trader_id}, side={self.side.name}, "
            f"price={self.price:.2f}, qty={self.quantity}, ts={self.timestamp})"
        )


class OrderCancelledEvent(MarketEvent):
    """Event emitted when a resting limit order is cancelled."""
    __slots__ = (
        'order_id',
        'trader_id',
        'side',
        'price',
        'remaining_quantity',
    )

    def __init__(
        self,
        order_id: int,
        trader_id: str,
        side: Side,
        price: float,
        remaining_quantity: int,
        timestamp: int,
    ) -> None:
        super().__init__(timestamp)
        self.order_id: int = order_id
        self.trader_id: str = trader_id
        self.side: Side = side
        self.price: float = price
        self.remaining_quantity: int = remaining_quantity

    def __repr__(self) -> str:
        return (
            f"OrderCancelled(id={self.order_id}, trader={self.trader_id}, side={self.side.name}, "
            f"price={self.price:.2f}, rem_qty={self.remaining_quantity}, ts={self.timestamp})"
        )


class TradeExecutedEvent(MarketEvent):
    """Event emitted when a trade is executed between a maker and taker."""
    __slots__ = (
        'trade_id',
        'maker_order_id',
        'taker_order_id',
        'price',
        'quantity',
        'maker_side',
    )

    def __init__(
        self,
        trade_id: int,
        maker_order_id: int,
        taker_order_id: int,
        price: float,
        quantity: int,
        timestamp: int,
        maker_side: Side,
    ) -> None:
        super().__init__(timestamp)
        self.trade_id: int = trade_id
        self.maker_order_id: int = maker_order_id
        self.taker_order_id: int = taker_order_id
        self.price: float = price
        self.quantity: int = quantity
        self.maker_side: Side = maker_side

    def __repr__(self) -> str:
        return (
            f"TradeExecuted(trade_id={self.trade_id}, maker={self.maker_order_id}, taker={self.taker_order_id}, "
            f"price={self.price:.2f}, qty={self.quantity}, maker_side={self.maker_side.name}, ts={self.timestamp})"
        )


class MarketDataGateway:
    """
    High-performance Level 2 Market Data Gateway and Event Broadcaster.
    
    Subscribers can attach callbacks to consume streaming trade, BBO, or raw event notifications.
    Maintains an in-memory sequential event journal for audit and replay verification.
    """
    __slots__ = (
        '_trade_subscribers',
        '_bbo_subscribers',
        '_event_subscribers',
        '_event_log',
        '_journal_enabled',
    )

    def __init__(self, journal_enabled: bool = True) -> None:
        self._trade_subscribers: list[Callable[[Trade], None]] = []
        self._bbo_subscribers: list[Callable[[BBO], None]] = []
        self._event_subscribers: list[Callable[[MarketEvent], None]] = []
        self._event_log: list[MarketEvent] = []
        self._journal_enabled: bool = journal_enabled

    @property
    def event_log(self) -> list[MarketEvent]:
        return self._event_log

    def clear_journal(self) -> None:
        self._event_log.clear()

    def subscribe_trade(self, callback: Callable[[Trade], None]) -> None:
        """Register a subscriber callback for executed trades."""
        self._trade_subscribers.append(callback)

    def subscribe_bbo(self, callback: Callable[[BBO], None]) -> None:
        """Register a subscriber callback for Top of Book (BBO) changes."""
        self._bbo_subscribers.append(callback)

    def subscribe_events(self, callback: Callable[[MarketEvent], None]) -> None:
        """Register a subscriber callback for all raw discrete events."""
        self._event_subscribers.append(callback)

    def on_trade_executed(self, trade: Trade) -> None:
        """Invoked by MatchingEngine when a match occurs."""
        for sub in self._trade_subscribers:
            sub(trade)

        event = TradeExecutedEvent(
            trade_id=trade.trade_id,
            maker_order_id=trade.maker_order_id,
            taker_order_id=trade.taker_order_id,
            price=trade.price,
            quantity=trade.quantity,
            timestamp=trade.timestamp,
            maker_side=trade.maker_side,
        )

        if self._journal_enabled:
            self._event_log.append(event)

        for sub in self._event_subscribers:
            sub(event)

    def on_order_placed(self, order: Order) -> None:
        """Invoked by MatchingEngine when a limit order rests in the book."""
        event = OrderPlacedEvent(
            order_id=order.order_id,
            trader_id=order.trader_id,
            side=order.side,
            price=order.price,
            quantity=order.quantity,
            timestamp=order.timestamp,
        )

        if self._journal_enabled:
            self._event_log.append(event)

        for sub in self._event_subscribers:
            sub(event)

    def on_order_cancelled(self, order: Order, timestamp: int = 0) -> None:
        """Invoked by MatchingEngine when an order is cancelled."""
        event = OrderCancelledEvent(
            order_id=order.order_id,
            trader_id=order.trader_id,
            side=order.side,
            price=order.price,
            remaining_quantity=order.quantity,
            timestamp=timestamp if timestamp != 0 else order.timestamp,
        )

        if self._journal_enabled:
            self._event_log.append(event)

        for sub in self._event_subscribers:
            sub(event)

    def broadcast_bbo(self, bbo: BBO) -> None:
        """Manually broadcast a BBO snapshot to subscribers."""
        for sub in self._bbo_subscribers:
            sub(bbo)
