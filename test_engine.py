"""
Unit Tests and Invariant Verification Suite for the Matching Engine.

Validates:
1. Strict Price-Time FIFO Execution Precedence.
2. Price Improvement & Aggressive Crossing.
3. Multi-Level Liquidity Sweeps for Market & Limit Orders.
4. O(1) Doubly-Linked List Order Cancellation (Head, Middle, Tail).
5. Level 2 Order Book Snapshots and Top-of-Book (BBO) Tracking.
6. System Conservation Invariants (Volume and State Consistency).
7. Market Data Gateway and Deterministic Event Journaling.
"""

import random
import pytest
from models import Side, OrderType, Order, Trade, BBO
from matching_engine import MatchingEngine, LimitLevel
from market_data import MarketDataGateway, OrderPlacedEvent, OrderCancelledEvent, TradeExecutedEvent


def test_price_time_fifo_precedence():
    """Verify that orders at the same price are matched strictly in FIFO submission order."""
    engine = MatchingEngine()

    # Place 3 resting Buy orders at the identical price of 100.0
    o1 = Order(order_id=1, trader_id="T1", side=Side.BUY, price=100.0, quantity=10, timestamp=1000)
    o2 = Order(order_id=2, trader_id="T2", side=Side.BUY, price=100.0, quantity=20, timestamp=1001)
    o3 = Order(order_id=3, trader_id="T3", side=Side.BUY, price=100.0, quantity=15, timestamp=1002)

    engine.add_limit_order(o1)
    engine.add_limit_order(o2)
    engine.add_limit_order(o3)

    assert engine.order_count == 3
    assert engine.total_resting_volume == 45

    # Incoming aggressive Sell limit order for 25 @ 100.0
    sell_taker = Order(order_id=4, trader_id="T4", side=Side.SELL, price=100.0, quantity=25, timestamp=1005)
    trades, resting = engine.add_limit_order(sell_taker)

    assert resting is None  # Fully filled
    assert len(trades) == 2

    # Trade 1: Full fill of o1 (qty=10)
    assert trades[0].maker_order_id == 1
    assert trades[0].taker_order_id == 4
    assert trades[0].quantity == 10
    assert trades[0].price == 100.0

    # Trade 2: Partial fill of o2 (qty=15)
    assert trades[1].maker_order_id == 2
    assert trades[1].taker_order_id == 4
    assert trades[1].quantity == 15
    assert trades[1].price == 100.0

    # Remaining in book: o2 (5 units remaining) and o3 (15 units remaining)
    assert engine.order_count == 2
    assert engine.total_resting_volume == 20
    assert engine.get_order(1) is None
    assert engine.get_order(2) is not None
    assert engine.get_order(2).quantity == 5
    assert engine.get_order(3).quantity == 15


def test_price_improvement_and_crossing():
    """Verify that an aggressive taker receives the resting maker's price (price improvement)."""
    engine = MatchingEngine()

    # Resting ask at 101.50
    ask_maker = Order(order_id=1, trader_id="M1", side=Side.SELL, price=101.50, quantity=50, timestamp=100)
    engine.add_limit_order(ask_maker)

    # Aggressive buy willing to pay up to 105.00
    buy_taker = Order(order_id=2, trader_id="T1", side=Side.BUY, price=105.00, quantity=20, timestamp=105)
    trades, resting = engine.add_limit_order(buy_taker)

    assert resting is None
    assert len(trades) == 1
    # Trade execution price must be maker's price (101.50), not taker's price (105.00)
    assert trades[0].price == 101.50
    assert trades[0].quantity == 20
    assert trades[0].maker_order_id == 1
    assert trades[0].taker_order_id == 2


def test_multi_level_market_order_sweep():
    """Verify that aggressive market orders sweep multiple price levels until filled."""
    engine = MatchingEngine()

    # Populate sell book across 3 distinct price levels
    engine.add_limit_order(Order(1, "S1", Side.SELL, 101.0, 10, 100))
    engine.add_limit_order(Order(2, "S2", Side.SELL, 102.0, 15, 101))
    engine.add_limit_order(Order(3, "S3", Side.SELL, 103.0, 20, 102))

    # Market buy for 30 shares
    trades, unfilled = engine.execute_market_order(Side.BUY, quantity=30, taker_order_id=99, trader_id="BUYER", timestamp=200)

    assert unfilled == 0
    assert len(trades) == 3

    # Level 1: 10 @ 101.0
    assert trades[0].price == 101.0
    assert trades[0].quantity == 10
    assert trades[0].maker_order_id == 1

    # Level 2: 15 @ 102.0
    assert trades[1].price == 102.0
    assert trades[1].quantity == 15
    assert trades[1].maker_order_id == 2

    # Level 3: 5 @ 103.0
    assert trades[2].price == 103.0
    assert trades[2].quantity == 5
    assert trades[2].maker_order_id == 3

    # Remaining in book: 15 shares at 103.0
    assert engine.best_ask_price == 103.0
    bbo = engine.get_bbo()
    assert bbo.ask_volume == 15
    assert engine.total_resting_volume == 15


def test_market_order_partial_exhaustion():
    """Verify market order behavior when liquidity in book is insufficient."""
    engine = MatchingEngine()

    engine.add_limit_order(Order(1, "B1", Side.BUY, 99.0, 10, 100))
    engine.add_limit_order(Order(2, "B2", Side.BUY, 98.0, 15, 101))

    # Market sell order for 35 shares (only 25 available)
    trades, unfilled = engine.execute_market_order(Side.SELL, quantity=35, taker_order_id=999, timestamp=200)

    assert unfilled == 10  # 35 - 25 = 10 unfilled
    assert sum(t.quantity for t in trades) == 25
    assert engine.order_count == 0
    assert engine.best_bid_price is None


def test_order_cancellation_o1_linked_list_edges():
    """Verify cancellation at Head, Middle, and Tail of a price level."""
    engine = MatchingEngine()

    o1 = Order(1, "T1", Side.BUY, 100.0, 10, 100)
    o2 = Order(2, "T2", Side.BUY, 100.0, 20, 101)
    o3 = Order(3, "T3", Side.BUY, 100.0, 30, 102)

    engine.add_limit_order(o1)
    engine.add_limit_order(o2)
    engine.add_limit_order(o3)

    level = o1.level
    assert level.order_count == 3
    assert level.total_volume == 60
    assert level.head == o1
    assert level.tail == o3

    # 1. Cancel Middle Order (o2)
    cancelled = engine.cancel_order(2)
    assert cancelled == o2
    assert engine.get_order(2) is None
    assert level.order_count == 2
    assert level.total_volume == 40
    assert o1.next == o3
    assert o3.prev == o1

    # 2. Cancel Head Order (o1)
    cancelled = engine.cancel_order(1)
    assert cancelled == o1
    assert level.order_count == 1
    assert level.total_volume == 30
    assert level.head == o3
    assert o3.prev is None

    # 3. Cancel Tail / Last Order (o3)
    cancelled = engine.cancel_order(3)
    assert cancelled == o3
    assert engine.order_count == 0
    assert engine.best_bid_price is None
    assert 100.0 not in engine._bids

    # 4. Cancel non-existent order
    assert engine.cancel_order(9999) is None


def test_l2_snapshot_and_bbo():
    """Verify Level 2 market depth snapshot and BBO calculations."""
    engine = MatchingEngine()

    # Bids
    engine.add_limit_order(Order(1, "B1", Side.BUY, 100.0, 100, 1))
    engine.add_limit_order(Order(2, "B2", Side.BUY, 100.0, 50, 2))  # Level 100.0 total = 150
    engine.add_limit_order(Order(3, "B3", Side.BUY, 99.5, 200, 3))
    engine.add_limit_order(Order(4, "B4", Side.BUY, 99.0, 300, 4))
    engine.add_limit_order(Order(5, "B5", Side.BUY, 98.5, 400, 5))

    # Asks
    engine.add_limit_order(Order(6, "A1", Side.SELL, 100.5, 80, 6))
    engine.add_limit_order(Order(7, "A2", Side.SELL, 101.0, 120, 7))
    engine.add_limit_order(Order(8, "A3", Side.SELL, 101.5, 250, 8))

    bbo = engine.get_bbo()
    assert bbo.bid_price == 100.0
    assert bbo.bid_volume == 150
    assert bbo.ask_price == 100.5
    assert bbo.ask_volume == 80
    assert bbo.spread == 0.5
    assert bbo.mid_price == 100.25

    # L2 Snapshot depth=3
    bids_l2, asks_l2 = engine.get_l2_snapshot(depth=3)
    assert bids_l2 == [(100.0, 150), (99.5, 200), (99.0, 300)]
    assert asks_l2 == [(100.5, 80), (101.0, 120), (101.5, 250)]


def test_market_data_gateway_pubsub_and_journal():
    """Verify event pub/sub dispatching and audit journal accuracy."""
    gateway = MarketDataGateway(journal_enabled=True)
    engine = MatchingEngine(market_data_gateway=gateway)

    received_trades: list[Trade] = []
    received_events = []

    gateway.subscribe_trade(lambda t: received_trades.append(t))
    gateway.subscribe_events(lambda e: received_events.append(e))

    # 1. Place Limit Sell
    o1 = Order(1, "M1", Side.SELL, 105.0, 50, 1000)
    engine.add_limit_order(o1)

    # 2. Place Limit Buy (Resting)
    o2 = Order(2, "M2", Side.BUY, 104.0, 30, 1001)
    engine.add_limit_order(o2)

    # 3. Aggressive Limit Buy Crossing o1
    o3 = Order(3, "T1", Side.BUY, 105.0, 20, 1002)
    engine.add_limit_order(o3)

    # 4. Cancel o2
    engine.cancel_order(2, timestamp=1003)

    assert len(received_trades) == 1
    assert received_trades[0].quantity == 20
    assert received_trades[0].price == 105.0

    # Verify Journal Events
    log = gateway.event_log
    assert len(log) == 4
    assert isinstance(log[0], OrderPlacedEvent)
    assert log[0].order_id == 1

    assert isinstance(log[1], OrderPlacedEvent)
    assert log[1].order_id == 2

    assert isinstance(log[2], TradeExecutedEvent)
    assert log[2].trade_id == 1
    assert log[2].maker_order_id == 1
    assert log[2].taker_order_id == 3

    assert isinstance(log[3], OrderCancelledEvent)
    assert log[3].order_id == 2


def test_system_volume_conservation_invariant():
    """
    Rigorously verifies the fundamental volume conservation invariant under a randomized fuzz test:
    Total Submitted Volume == Resting Book Volume + (2 * Cumulative Executed Trade Volume) + Cancelled Volume + Unfilled Market Volume.
    """
    random.seed(42)
    engine = MatchingEngine()

    total_submitted_buy_qty = 0
    total_submitted_sell_qty = 0
    total_traded_buy_qty = 0
    total_traded_sell_qty = 0
    total_cancelled_buy_qty = 0
    total_cancelled_sell_qty = 0
    total_unfilled_market_buy_qty = 0
    total_unfilled_market_sell_qty = 0

    active_order_ids: list[int] = []
    order_side_map: dict[int, Side] = {}

    for i in range(1, 2001):
        action = random.choices(["LIMIT", "MARKET", "CANCEL"], weights=[0.65, 0.20, 0.15])[0]
        side = random.choice([Side.BUY, Side.SELL])
        price = round(random.uniform(90.0, 110.0), 1)
        qty = random.randint(1, 50)

        if action == "LIMIT":
            order = Order(order_id=i, trader_id=f"TR_{i%10}", side=side, price=price, quantity=qty, timestamp=i)
            if side == Side.BUY:
                total_submitted_buy_qty += qty
            else:
                total_submitted_sell_qty += qty

            trades, resting = engine.add_limit_order(order)
            for t in trades:
                total_traded_buy_qty += t.quantity
                total_traded_sell_qty += t.quantity

            if resting is not None:
                active_order_ids.append(i)
                order_side_map[i] = side

        elif action == "MARKET":
            if side == Side.BUY:
                total_submitted_buy_qty += qty
            else:
                total_submitted_sell_qty += qty

            trades, unfilled = engine.execute_market_order(side=side, quantity=qty, taker_order_id=i, timestamp=i)
            for t in trades:
                total_traded_buy_qty += t.quantity
                total_traded_sell_qty += t.quantity

            if side == Side.BUY:
                total_unfilled_market_buy_qty += unfilled
            else:
                total_unfilled_market_sell_qty += unfilled

        elif action == "CANCEL" and active_order_ids:
            target_id = active_order_ids.pop(random.randrange(len(active_order_ids)))
            cancelled_order = engine.cancel_order(target_id, timestamp=i)
            if cancelled_order is not None:
                if cancelled_order.side == Side.BUY:
                    total_cancelled_buy_qty += cancelled_order.quantity
                else:
                    total_cancelled_sell_qty += cancelled_order.quantity

    # Calculate resting volumes in book
    resting_bid_volume = sum(level.total_volume for level in engine._bids.values())
    resting_ask_volume = sum(level.total_volume for level in engine._asks.values())

    # Invariant 1: Buy Side Conservation
    assert total_submitted_buy_qty == total_traded_buy_qty + resting_bid_volume + total_cancelled_buy_qty + total_unfilled_market_buy_qty, (
        f"Buy Volume mismatch: Submitted({total_submitted_buy_qty}) != Traded({total_traded_buy_qty}) + "
        f"Resting({resting_bid_volume}) + Cancelled({total_cancelled_buy_qty}) + Unfilled({total_unfilled_market_buy_qty})"
    )

    # Invariant 2: Sell Side Conservation
    assert total_submitted_sell_qty == total_traded_sell_qty + resting_ask_volume + total_cancelled_sell_qty + total_unfilled_market_sell_qty, (
        f"Sell Volume mismatch: Submitted({total_submitted_sell_qty}) != Traded({total_traded_sell_qty}) + "
        f"Resting({resting_ask_volume}) + Cancelled({total_cancelled_sell_qty}) + Unfilled({total_unfilled_market_sell_qty})"
    )

    # Invariant 3: Traded volume symmetry (every trade has exactly 1 buyer and 1 seller)
    assert total_traded_buy_qty == total_traded_sell_qty
