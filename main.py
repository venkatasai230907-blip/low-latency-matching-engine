"""
Low-Latency Order Matching Engine and Market Data Gateway.

Demonstration and benchmarking runner:
1. Live simulated exchange order book ladder and real-time trade tape.
2. Ingestion micro-benchmark of 100,000 mixed orders with latency profiling.
3. Formatted systems performance report and latency distribution artifact generation.
"""

from __future__ import annotations
import sys
import time
from models import Side, OrderType, Order, Trade, BBO
from matching_engine import MatchingEngine
from market_data import MarketDataGateway
from benchmark import run_benchmark, plot_latency_profile


def render_l2_book(engine: MatchingEngine, depth: int = 5) -> None:
    """Render a clean formatted Level 2 Depth Ladder in the terminal."""
    bids, asks = engine.get_l2_snapshot(depth=depth)
    bbo = engine.get_bbo()

    print("\n+---------------------------------------------------------+")
    print("|               LEVEL 2 ORDER BOOK SNAPSHOT               |")
    print("+---------------------------------------------------------+")
    print("|    SIDE    |     PRICE ($)     |   VOLUME   |   ORDERS  |")
    print("+------------+-------------------+------------+-----------+")

    # Asks displayed top-to-bottom (highest ask down to best ask)
    if asks:
        for price, vol in reversed(asks):
            level = engine._asks.get(price)
            order_count = level.order_count if level else 0
            is_best = (price == bbo.ask_price)
            flag = " [BEST ASK]" if is_best else ""
            print(f"| ASK (SELL) | {price:>13.2f}     | {vol:>10,d} | {order_count:>7d}  |{flag}")
    else:
        print("| ASK (SELL) |      EMPTY        |      -     |     -     |")

    print("+------------+-------------------+------------+-----------+")
    if bbo.spread is not None:
        print(f"|   SPREAD   | {bbo.spread:>13.2f}     | MID: {bbo.mid_price:>6.2f} |           |")
    else:
        print("|   SPREAD   |       N/A         | MID:   N/A |           |")
    print("+------------+-------------------+------------+-----------+")

    # Bids displayed from best bid down
    if bids:
        for price, vol in bids:
            level = engine._bids.get(price)
            order_count = level.order_count if level else 0
            is_best = (price == bbo.bid_price)
            flag = " [BEST BID]" if is_best else ""
            print(f"| BID (BUY)  | {price:>13.2f}     | {vol:>10,d} | {order_count:>7d}  |{flag}")
    else:
        print("| BID (BUY)  |      EMPTY        |      -     |     -     |")

    print("+---------------------------------------------------------+\n")


def run_live_simulation() -> None:
    """Demonstrate order flow, matching behavior, and event logging with terminal visualization."""
    print("=" * 65)
    print("   STAGE 1: INTERACTIVE EXCHANGE SIMULATION & L2 BOOK DEMO")
    print("=" * 65)

    gateway = MarketDataGateway(journal_enabled=True)
    engine = MatchingEngine(market_data_gateway=gateway)

    trade_tape: list[str] = []
    gateway.subscribe_trade(
        lambda t: trade_tape.append(
            f" [EXEC] Trade #{t.trade_id:03d}: {t.quantity} shares @ ${t.price:.2f} "
            f"(Maker Order #{t.maker_order_id} vs Taker Order #{t.taker_order_id})"
        )
    )

    print("\n[Step 1] Seeding Initial Passive Liquidity (Bids & Asks)...")
    seed_orders = [
        # Asks
        Order(101, "MM_CITADEL", Side.SELL, 102.50, 200, 1),
        Order(102, "MM_JANE", Side.SELL, 102.00, 150, 2),
        Order(103, "MM_JUMP", Side.SELL, 101.50, 100, 3),
        Order(104, "MM_OPTIVER", Side.SELL, 101.00, 50, 4),
        # Bids
        Order(201, "MM_OPTIVER", Side.BUY, 100.00, 50, 5),
        Order(202, "MM_JUMP", Side.BUY, 99.50, 100, 6),
        Order(203, "MM_JANE", Side.BUY, 99.00, 150, 7),
        Order(204, "MM_CITADEL", Side.BUY, 98.50, 200, 8),
    ]

    for o in seed_orders:
        engine.add_limit_order(o)

    render_l2_book(engine)

    print("\n[Step 2] Incoming Aggressive Limit Buy (Crossing Spread): 80 shares @ $101.50")
    aggressive_buy = Order(301, "ALGO_TAKER", Side.BUY, 101.50, 80, 10)
    trades, resting = engine.add_limit_order(aggressive_buy)
    
    for log_msg in trade_tape:
        print(log_msg)
    trade_tape.clear()
    
    print(f" -> Aggressive order fill result: {len(trades)} trades executed. Resting in book: {resting is not None}")
    render_l2_book(engine)

    print("\n[Step 3] Incoming Aggressive Market Sell Order: 220 shares")
    trades, unfilled = engine.execute_market_order(Side.SELL, quantity=220, taker_order_id=401, trader_id="HFT_SWEEPER", timestamp=20)
    
    for log_msg in trade_tape:
        print(log_msg)
    trade_tape.clear()
    
    print(f" -> Market order executed across multiple bid levels. Unfilled balance: {unfilled}")
    render_l2_book(engine)

    print("\n[Step 4] Order Cancellation: Cancelling Resting Ask #101 (200 shares @ $102.50)")
    cancelled = engine.cancel_order(101, timestamp=30)
    print(f" -> Cancelled: {cancelled}")
    render_l2_book(engine)

    print(f"[+] Total Events Recorded in Gateway Journal: {len(gateway.event_log)}")


def print_metrics_table(result) -> None:
    """Print formatted systems benchmark metrics table."""
    print("\n" + "=" * 65)
    print("           STAGE 2: 100,000 ORDER BENCHMARK REPORT          ")
    print("=" * 65)
    print(f" | {'METRIC':<30} | {'VALUE':<26} |")
    print(" +--------------------------------+----------------------------+")
    print(f" | Total Workload Operations      | {result.total_operations:>20,d} ops |")
    print(f" | Total Engine Execution Time    | {result.total_time_sec * 1000:>20.2f} ms  |")
    print(f" | Engine Throughput              | {result.throughput_ops_sec:>19,.0f} ops/s |")
    print(" +--------------------------------+----------------------------+")
    print(f" | Mean Tick-to-Trade Latency     | {result.mean_us:>21.2f} us |")
    print(f" | Latency Std Deviation          | {result.std_us:>21.2f} us |")
    print(f" | Min Latency                    | {result.min_us:>21.2f} us |")
    print(f" | Max Latency                    | {result.max_us:>21.2f} us |")
    print(" +--------------------------------+----------------------------+")
    print(f" | p50 Latency (Median)           | {result.p50_us:>21.2f} us |")
    print(f" | p90 Latency                    | {result.p90_us:>21.2f} us |")
    print(f" | p95 Latency                    | {result.p95_us:>21.2f} us |")
    print(f" | p99 Latency (Tail)             | {result.p99_us:>21.2f} us |")
    print(f" | p99.9 Latency (Extreme Tail)   | {result.p99_9_us:>21.2f} us |")
    print("=" * 65)


def main() -> None:
    # 1. Run live simulation
    run_live_simulation()

    # 2. Run benchmark
    print("\n" + "=" * 65)
    print("   STAGE 2: RUNNING MICRO-BENCHMARK (100,000 ORDERS)")
    print("=" * 65)
    result, _ = run_benchmark(num_orders=100_000)
    print_metrics_table(result)

    # 3. Generate plot
    plot_latency_profile(result, output_filepath="latency_profile.png")


if __name__ == "__main__":
    main()
