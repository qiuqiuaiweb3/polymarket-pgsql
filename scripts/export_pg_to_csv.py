#!/usr/bin/env python3
"""
Export Postgres tables to CSV files in project root.

Defaults are chosen for long-running collectors:
- export last N hours of tick-like tables (asset_price_ticks, arb_signals)
- export full small tables (asset_price_latest, paper_pnl)

Examples:
  # export last 3 hours (ticks + signals) and full latest/pnl
  PYTHONPATH=src python scripts/export_pg_to_csv.py --since-hours 3

  # export last 24 hours and include raw json columns
  PYTHONPATH=src python scripts/export_pg_to_csv.py --since-hours 24 --include-raw
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg
from dotenv import load_dotenv

from polymarket_pgsql.config import load_settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts_for_filename(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S-UTC")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since-hours", type=float, default=3.0, help="导出最近 N 小时的数据（对 ticks/signals 生效）")
    p.add_argument(
        "--out-dir",
        type=str,
        default=".",
        help="CSV 输出目录（默认项目根目录）",
    )
    p.add_argument(
        "--include-raw",
        action="store_true",
        help="是否包含 raw(jsonb) 列（会显著增大 CSV 体积）",
    )
    p.add_argument(
        "--event-id",
        type=int,
        default=45883,
        help="用于过滤 paper_pnl/arb_signals 的 event_id（默认 45883）",
    )
    return p.parse_args()


def export_query_to_csv(conn: psycopg.Connection, *, sql: str, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Use standard CSV copy
    with conn.cursor() as cur, open(out_path, "w", encoding="utf-8", newline="") as f:
        with cur.copy(sql) as copy:
            for data in copy:
                if isinstance(data, memoryview):
                    data = bytes(data)
                f.write(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data))


def main() -> int:
    load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)
    s = load_settings()
    args = parse_args()

    now = utc_now()
    since_expr = f"now() - interval '{args.since_hours} hours'"

    ts = fmt_ts_for_filename(now)
    out_dir = args.out_dir

    # Select columns (raw is large; default off)
    raw_cols = ", raw" if args.include_raw else ""

    queries = [
        (
            f"{out_dir}/asset_price_latest_{ts}.csv",
            f"""
            copy (
              select asset_id, market_id, outcome, as_of, best_bid, best_ask, mid, source{raw_cols}, updated_at
              from asset_price_latest
              order by market_id, outcome
            ) to stdout with csv header
            """,
        ),
        (
            f"{out_dir}/paper_pnl_{ts}.csv",
            f"""
            copy (
              select event_id, realized_pnl, unrealized_pnl, updated_at
              from paper_pnl
              where event_id = {int(args.event_id)}
              order by event_id
            ) to stdout with csv header
            """,
        ),
        (
            f"{out_dir}/arb_signals_last_{args.since_hours}h_{ts}.csv",
            f"""
            copy (
              select signal_id, event_id, as_of, kind, edge, detail, created_at
              from arb_signals
              where event_id = {int(args.event_id)}
                and as_of >= {since_expr}
              order by as_of asc
            ) to stdout with csv header
            """,
        ),
        (
            f"{out_dir}/asset_price_ticks_last_{args.since_hours}h_{ts}.csv",
            f"""
            copy (
              select asset_id, as_of, market_id, outcome, best_bid, best_ask, mid, source{raw_cols}
              from asset_price_ticks
              where as_of >= {since_expr}
              order by as_of asc
            ) to stdout with csv header
            """,
        ),
    ]

    print(f"Connecting to DB to export csvs (since {args.since_hours}h)...")
    try:
        with psycopg.connect(s.database_url) as conn:
            for out_path, sql in queries:
                print(f"Exporting to {out_path} ...", end="", flush=True)
                try:
                    export_query_to_csv(conn, sql=sql, out_path=out_path)
                    print("DONE")
                except Exception as e:
                    print(f"FAILED: {e}")
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

