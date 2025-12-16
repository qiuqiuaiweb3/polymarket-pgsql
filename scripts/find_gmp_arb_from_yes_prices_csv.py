#!/usr/bin/env python3
"""
Scan a Polymarket multi-outcome (GMP-style) CSV where each row contains
minute-level Buy-YES prices (best ask) for each outcome, and detect bundle-YES
arbitrage windows where sum(YES) < 1 (optionally after fees).

Expected CSV format (like the user's file):
  "Date (UTC)","Timestamp (UTC)","Outcome A","Outcome B",...
  "12-12-2025 16:00","1765555207","0.0115","0.215",...

Important:
  - This script assumes the outcome columns are Buy YES (best ask). If your CSV
    is mid/last/implied probability, treat results as theoretical only.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Row:
    ts: int
    date_str: str
    yes_prices: List[float]
    sum_yes: float


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def read_rows(path: str) -> Tuple[List[str], List[Row], int]:
    rows: List[Row] = []
    nan_rows = 0
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        outcome_cols = header[2:]
        for line in r:
            if len(line) < 3:
                nan_rows += 1
                continue
            date_s = line[0]
            ts_s = line[1]
            yes: List[float] = []
            ok = True
            for x in line[2:]:
                try:
                    v = float(x)
                except Exception:
                    ok = False
                    break
                if math.isnan(v):
                    ok = False
                    break
                yes.append(v)
            if not ok:
                nan_rows += 1
                continue
            try:
                ts = int(float(ts_s))
            except Exception:
                nan_rows += 1
                continue
            s = sum(yes)
            rows.append(Row(ts=ts, date_str=date_s, yes_prices=yes, sum_yes=s))
    rows.sort(key=lambda x: x.ts)
    return outcome_cols, rows, nan_rows


def find_intervals(
    rows: List[Row],
    *,
    eps: float,
    fee_rate: float,
    fee_fixed: float,
    max_gap_seconds: int = 90,
) -> List[Tuple[Row, Row, int, float, float, Row]]:
    """
    Returns intervals as tuples:
      (start_row, end_row, minutes_count, max_edge, avg_edge, max_edge_row)
    """
    def net_edge(r: Row) -> float:
        # Fee model:
        # - fee_rate: proportional fee applied to notional (price * qty)
        # - fee_fixed: fixed fee per leg/order
        total_cost = r.sum_yes * (1.0 + fee_rate) + (len(r.yes_prices) * fee_fixed)
        return 1.0 - total_cost

    arb = [r for r in rows if net_edge(r) > eps]
    if not arb:
        return []

    intervals = []
    start = arb[0]
    prev = arb[0]
    max_edge = net_edge(arb[0])
    max_row = arb[0]
    sum_edge = net_edge(arb[0])
    n = 1

    for cur in arb[1:]:
        if cur.ts - prev.ts <= max_gap_seconds:
            prev = cur
            n += 1
            edge = net_edge(cur)
            sum_edge += edge
            if edge > max_edge:
                max_edge = edge
                max_row = cur
        else:
            intervals.append((start, prev, n, max_edge, sum_edge / n, max_row))
            start = cur
            prev = cur
            max_edge = net_edge(cur)
            max_row = cur
            sum_edge = net_edge(cur)
            n = 1

    intervals.append((start, prev, n, max_edge, sum_edge / n, max_row))
    return intervals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path to CSV")
    ap.add_argument("--eps", type=float, default=1e-12, help="Strictness for sum_yes < 1-eps")
    ap.add_argument("--top", type=int, default=15, help="How many intervals to print")
    ap.add_argument(
        "--fee-rate",
        type=float,
        default=0.0,
        help="Proportional fee rate applied on notional (e.g. 0.002 for 0.2%)",
    )
    ap.add_argument(
        "--fee-fixed",
        type=float,
        default=0.0,
        help="Fixed fee per leg/order (same unit as prices, e.g. 0.0001 = 1bp of $1)",
    )
    args = ap.parse_args()

    outcome_cols, rows, nan_rows = read_rows(args.csv_path)
    if not rows:
        print("No valid rows parsed.")
        return 2

    min_row = min(rows, key=lambda r: r.sum_yes)
    max_row = max(rows, key=lambda r: r.sum_yes)
    def net_edge(r: Row) -> float:
        total_cost = r.sum_yes * (1.0 + args.fee_rate) + (len(r.yes_prices) * args.fee_fixed)
        return 1.0 - total_cost

    arb_count = sum(1 for r in rows if net_edge(r) > args.eps)

    print("rows:", len(rows))
    print("outcomes:", outcome_cols)
    print("invalid_rows_skipped:", nan_rows)
    print("min_sum_yes:", f"{min_row.sum_yes:.8f}", "at", fmt_ts(min_row.ts))
    print("max_sum_yes:", f"{max_row.sum_yes:.8f}", "at", fmt_ts(max_row.ts))
    print("fee_rate:", args.fee_rate, "fee_fixed_per_leg:", args.fee_fixed)
    print("arb_rows(net_edge>0):", arb_count)

    intervals = find_intervals(rows, eps=args.eps, fee_rate=args.fee_rate, fee_fixed=args.fee_fixed)
    intervals_sorted = sorted(intervals, key=lambda t: t[3], reverse=True)
    print("\nTop intervals by max edge:")
    for start, end, n, max_edge, avg_edge, max_edge_row in intervals_sorted[: args.top]:
        print(
            f"{fmt_ts(start.ts)} -> {fmt_ts(end.ts)} | {n:4d} min | "
            f"max_edge={max_edge:.4%} avg_edge={avg_edge:.4%} | "
            f"max_at={fmt_ts(max_edge_row.ts)} sum_yes={max_edge_row.sum_yes:.6f}"
        )

    best = max((r for r in rows if net_edge(r) > args.eps), key=lambda r: net_edge(r), default=None)
    if best:
        print("\nBest single minute:")
        print("time:", fmt_ts(best.ts))
        print("sum_yes:", f"{best.sum_yes:.8f}", "net_edge:", f"{net_edge(best):.4%}")
        print("yes_prices:", ", ".join(f"{v:.6f}" for v in best.yes_prices))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


