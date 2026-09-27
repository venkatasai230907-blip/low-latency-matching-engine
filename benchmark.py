"""
Micro-Benchmarking and Latency Profiling Harness.

Simulates high-throughput production order flow (100,000 operations):
- Pre-allocates order streams to isolate pure engine execution latency from generator overhead.
- Measures individual operation latency using monotonic nanosecond timers (time.perf_counter_ns).
- Computes latency percentiles (p50, p90, p99, p99.9), mean, std, and aggregate throughput.
- Generates high-fidelity empirical CDF and latency distribution plots saved to `latency_profile.png`.
"""

from __future__ import annotations
import time
import random
import numpy as np
import matplotlib.pyplot as plt
from typing import NamedTuple, Any

from models import Side, OrderType, Order
from matching_engine import MatchingEngine


class BenchmarkResult(NamedTuple):
    total_operations: int
    total_time_sec: float
    throughput_ops_sec: float
    latencies_us: np.ndarray
    mean_us: float
    std_us: float
    min_us: float
    max_us: float
    p50_us: float
    p90_us: float
    p95_us: float
    p99_us: float
    p99_9_us: float


def generate_workload(
    num_orders: int = 100_000,
    initial_mid_price: float = 100.0,
    seed: int = 42,
) -> list[tuple[str, Any]]:
    """
    Pre-generate a realistic high-throughput order flow.
    
    Mix:
    - 70% Limit Orders (resting quotes and crossing liquidity)
    - 15% Market Orders (immediate liquidity sweeps)
    - 15% Order Cancellations (simulating high-frequency quote replacement)
    """
    rng = random.Random(seed)
    operations: list[tuple[str, Any]] = []
    
    active_ids: list[int] = []
    current_mid = initial_mid_price
    
    for i in range(1, num_orders + 1):
        # Determine operation type
        roll = rng.random()
        if roll < 0.70 or not active_ids:
            # Limit Order
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            # Random walk for mid price
            current_mid += rng.gauss(0, 0.05)
            spread_offset = rng.uniform(0.01, 2.50)
            
            if side == Side.BUY:
                price = round(current_mid - spread_offset + (0.5 if rng.random() < 0.2 else -0.1), 2)
            else:
                price = round(current_mid + spread_offset - (0.5 if rng.random() < 0.2 else -0.1), 2)
                
            price = max(0.01, price)
            qty = rng.randint(5, 100)
            order = Order(
                order_id=i,
                trader_id=f"TRADER_{i % 50}",
                side=side,
                price=price,
                quantity=qty,
                timestamp=i,
                order_type=OrderType.LIMIT,
            )
            operations.append(("LIMIT", order))
            active_ids.append(i)

        elif roll < 0.85:
            # Market Order
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            qty = rng.randint(5, 50)
            operations.append(("MARKET", (i, side, qty)))

        else:
            # Cancellation
            cancel_idx = rng.randrange(len(active_ids))
            target_id = active_ids.pop(cancel_idx)
            operations.append(("CANCEL", target_id))

    return operations


def run_benchmark(
    num_orders: int = 100_000,
    seed: int = 42,
    warmup_ops: int = 5_000,
) -> tuple[BenchmarkResult, MatchingEngine]:
    """
    Execute benchmark on the matching engine and compute latency statistics.
    """
    print(f"[*] Pre-generating {num_orders:,} order stream...")
    workload = generate_workload(num_orders=num_orders, seed=seed)
    
    # Warmup
    print(f"[*] Warming up JIT / cache with {warmup_ops:,} ops...")
    warmup_engine = MatchingEngine()
    for op_type, data in workload[:warmup_ops]:
        if op_type == "LIMIT":
            warmup_engine.add_limit_order(data)
        elif op_type == "MARKET":
            oid, side, qty = data
            warmup_engine.execute_market_order(side=side, quantity=qty, taker_order_id=oid)
        elif op_type == "CANCEL":
            warmup_engine.cancel_order(data)
    
    # Target Benchmark Run
    print(f"[*] Executing {num_orders:,} matching operations...")
    engine = MatchingEngine()
    latencies_ns = np.zeros(num_orders, dtype=np.int64)

    total_start = time.perf_counter_ns()
    
    for idx, (op_type, data) in enumerate(workload):
        t0 = time.perf_counter_ns()
        
        if op_type == "LIMIT":
            engine.add_limit_order(data)
        elif op_type == "MARKET":
            oid, side, qty = data
            engine.execute_market_order(side=side, quantity=qty, taker_order_id=oid)
        elif op_type == "CANCEL":
            engine.cancel_order(data)
            
        t1 = time.perf_counter_ns()
        latencies_ns[idx] = t1 - t0

    total_end = time.perf_counter_ns()
    total_time_sec = (total_end - total_start) / 1e9
    throughput = num_orders / total_time_sec

    # Convert to microseconds (us)
    latencies_us = latencies_ns / 1000.0

    result = BenchmarkResult(
        total_operations=num_orders,
        total_time_sec=total_time_sec,
        throughput_ops_sec=throughput,
        latencies_us=latencies_us,
        mean_us=float(np.mean(latencies_us)),
        std_us=float(np.std(latencies_us)),
        min_us=float(np.min(latencies_us)),
        max_us=float(np.max(latencies_us)),
        p50_us=float(np.percentile(latencies_us, 50)),
        p90_us=float(np.percentile(latencies_us, 90)),
        p95_us=float(np.percentile(latencies_us, 95)),
        p99_us=float(np.percentile(latencies_us, 99)),
        p99_9_us=float(np.percentile(latencies_us, 99.9)),
    )

    return result, engine


def plot_latency_profile(
    result: BenchmarkResult,
    output_filepath: str = "latency_profile.png",
) -> None:
    """
    Generate professional-grade quant systems latency profile visualization:
    1. Cumulative Distribution Function (CDF) with p50, p99, p99.9 indicators.
    2. High-resolution Latency Histogram with statistical summary.
    """
    plt.style.use("seaborn-v0_8-darkgrid" if "seaborn-v0_8-darkgrid" in plt.style.available else "default")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6), dpi=300)
    fig.patch.set_facecolor("#0E1117")

    for ax in (ax1, ax2):
        ax.set_facecolor("#161B22")
        ax.tick_params(colors="#C9D1D9", labelsize=10)
        ax.xaxis.label.set_color("#E6EDF3")
        ax.yaxis.label.set_color("#E6EDF3")
        ax.title.set_color("#58A6FF")
        for spine in ax.spines.values():
            spine.set_color("#30363D")

    # 1. CDF Plot
    sorted_latencies = np.sort(result.latencies_us)
    cdf = np.arange(1, len(sorted_latencies) + 1) / len(sorted_latencies)
    
    ax1.plot(sorted_latencies, cdf, color="#58A6FF", linewidth=2.2, label="Empirical CDF")
    
    # Mark percentiles on CDF
    ax1.axvline(result.p50_us, color="#2EA043", linestyle="--", linewidth=1.5, label=f"p50: {result.p50_us:.2f} µs")
    ax1.axvline(result.p90_us, color="#DB6D28", linestyle="--", linewidth=1.5, label=f"p90: {result.p90_us:.2f} µs")
    ax1.axvline(result.p99_us, color="#F85149", linestyle="--", linewidth=1.5, label=f"p99: {result.p99_us:.2f} µs")
    ax1.axvline(result.p99_9_us, color="#A371F7", linestyle=":", linewidth=1.5, label=f"p99.9: {result.p99_9_us:.2f} µs")

    # Zoom in to 99.95 percentile for clear viewing without extreme outliers
    cap_x = float(np.percentile(result.latencies_us, 99.95)) * 1.2
    ax1.set_xlim(0, max(cap_x, result.p99_us * 1.5))
    ax1.set_ylim(0, 1.02)
    ax1.set_title("Matching Engine Latency CDF (100k Orders)", fontsize=13, fontweight="bold", pad=12)
    ax1.set_xlabel("Tick-to-Trade Latency (µs)", fontsize=11, fontweight="bold")
    ax1.set_ylabel("Cumulative Probability", fontsize=11, fontweight="bold")
    ax1.legend(facecolor="#21262D", edgecolor="#30363D", labelcolor="#E6EDF3", fontsize=9, loc="lower right")

    # 2. Histogram / Latency Distribution
    # Filter for main distribution visualization
    hist_cap = float(np.percentile(result.latencies_us, 99.5))
    filtered_latencies = result.latencies_us[result.latencies_us <= hist_cap]
    
    counts, bins, _ = ax2.hist(
        filtered_latencies,
        bins=60,
        density=True,
        color="#388BFD",
        alpha=0.65,
        edgecolor="#1F6FEB",
        label="Density",
    )

    ax2.axvline(result.p50_us, color="#2EA043", linestyle="--", linewidth=1.5, label=f"p50: {result.p50_us:.2f} µs")
    ax2.axvline(result.p99_us, color="#F85149", linestyle="--", linewidth=1.5, label=f"p99: {result.p99_us:.2f} µs")

    ax2.set_title("Latency Density Distribution (Hot Path)", fontsize=13, fontweight="bold", pad=12)
    ax2.set_xlabel("Latency (µs)", fontsize=11, fontweight="bold")
    ax2.set_ylabel("Probability Density", fontsize=11, fontweight="bold")
    ax2.legend(facecolor="#21262D", edgecolor="#30363D", labelcolor="#E6EDF3", fontsize=9, loc="upper right")

    # Statistics Box
    stats_text = (
        f"Systems Metrics:\n"
        f"-------------------------\n"
        f"Throughput: {result.throughput_ops_sec:,.0f} ops/sec\n"
        f"Total Ops:  {result.total_operations:,}\n"
        f"Mean:       {result.mean_us:.2f} µs\n"
        f"Std Dev:    {result.std_us:.2f} µs\n"
        f"p50:        {result.p50_us:.2f} µs\n"
        f"p90:        {result.p90_us:.2f} µs\n"
        f"p99:        {result.p99_us:.2f} µs\n"
        f"p99.9:      {result.p99_9_us:.2f} µs"
    )
    ax2.text(
        0.55, 0.45,
        stats_text,
        transform=ax2.transAxes,
        fontsize=9.5,
        verticalalignment="center",
        fontfamily="monospace",
        color="#E6EDF3",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#21262D", edgecolor="#58A6FF", alpha=0.9),
    )

    plt.tight_layout()
    plt.savefig(output_filepath, dpi=300, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"[+] Latency distribution profile saved to {output_filepath}")


if __name__ == "__main__":
    benchmark_res, _ = run_benchmark(num_orders=100_000)
    print("\n" + "=" * 55)
    print(f" Benchmark Summary ({benchmark_res.total_operations:,} Operations)")
    print("=" * 55)
    print(f" Throughput:         {benchmark_res.throughput_ops_sec:>12,.0f} ops/sec")
    print(f" Total Elapsed Time: {benchmark_res.total_time_sec * 1000:>12.2f} ms")
    print(f" Mean Latency:       {benchmark_res.mean_us:>12.2f} us")
    print(f" p50 (Median):       {benchmark_res.p50_us:>12.2f} us")
    print(f" p90 Latency:        {benchmark_res.p90_us:>12.2f} us")
    print(f" p95 Latency:        {benchmark_res.p95_us:>12.2f} us")
    print(f" p99 Latency:        {benchmark_res.p99_us:>12.2f} us")
    print(f" p99.9 Latency:      {benchmark_res.p99_9_us:>12.2f} us")
    print("=" * 55)
    plot_latency_profile(benchmark_res)
