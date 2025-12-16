#!/usr/bin/env python3
"""
Subscribe Polymarket CLOB Market Channel for a set of markets, compute GMP (buy-YES basket)
arbitrage condition, and run a simple paper trading loop.

Goal (docs/今日目标.md):
- EVENT ID 45883
- market ids: 601697, 601698, 601699, 601700
- condition: sum(YES prices) < 1
- print realtime: time, prices, condition, paper trading PnL
"""

from __future__ import annotations

import argparse
import asyncio
import os
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from polymarket_pgsql.clob_ws import OrderBookState, market_channel_stream
from polymarket_pgsql.config import load_settings
from polymarket_pgsql.gamma_client import GammaClient
from polymarket_pgsql.pg_writer import PgWriter


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def d(x: float | str | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class MarketTokens:
    market_id: int
    question: str
    yes_asset_id: str
    no_asset_id: str


@dataclass
class BasketPosition:
    qty_per_leg: Decimal
    entry_yes_prices: Dict[int, Decimal]  # market_id -> fill price
    entry_fees: Dict[int, Decimal]  # market_id -> fee
    opened_at: datetime


def load_clob_auth_from_env() -> Optional[Dict[str, str]]:
    """
    Market channel is typically public, but docs show an optional auth object.
    Support both naming styles:
    - apiKey/secret/passphrase
    - CLOB_API_KEY/CLOB_API_SECRET/CLOB_API_PASSPHRASE
    """
    api_key = os.getenv("CLOB_API_KEY") or os.getenv("CLOB_APIKEY") or os.getenv("API_KEY")
    api_secret = os.getenv("CLOB_API_SECRET") or os.getenv("CLOB_SECRET") or os.getenv("API_SECRET")
    api_passphrase = (
        os.getenv("CLOB_API_PASSPHRASE") or os.getenv("CLOB_PASSPHRASE") or os.getenv("API_PASSPHRASE")
    )

    if not api_key and not api_secret and not api_passphrase:
        return None

    # Polymarket docs often use: {"apiKey": "...", "secret": "...", "passphrase": "..."}
    return {"apiKey": api_key or "", "secret": api_secret or "", "passphrase": api_passphrase or ""}


def fetch_market_tokens(gamma_base_url: str, market_ids: List[int]) -> List[MarketTokens]:
    c = GammaClient(gamma_base_url)
    out: List[MarketTokens] = []
    try:
        for mid in market_ids:
            m = c.get_market(mid)
            if not isinstance(m, dict):
                raise RuntimeError(f"Gamma /markets/{mid} 返回非 dict：{type(m)}")

            question = str(m.get("question") or "")
            clob_ids = m.get("clobTokenIds")
            outcomes = m.get("outcomes")

            # Gamma 有时会把数组字段作为 JSON 字符串返回（例如 '["...","..."]'）
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    pass
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    pass

            if not (isinstance(clob_ids, list) and len(clob_ids) >= 2):
                raise RuntimeError(f"market {mid} clobTokenIds 非数组或长度不足: {clob_ids}")
            if not (isinstance(outcomes, list) and len(outcomes) >= 2):
                raise RuntimeError(f"market {mid} outcomes 非数组或长度不足: {outcomes}")

            # 该 event 下是标准二元 market：outcomes=["Yes","No"]，clobTokenIds 顺序一致
            yes_idx = next((i for i, o in enumerate(outcomes) if str(o).lower() == "yes"), None)
            no_idx = next((i for i, o in enumerate(outcomes) if str(o).lower() == "no"), None)
            if yes_idx is None or no_idx is None:
                raise RuntimeError(f"market {mid} outcomes 非 Yes/No：{outcomes}")

            out.append(
                MarketTokens(
                    market_id=mid,
                    question=question,
                    yes_asset_id=str(clob_ids[yes_idx]),
                    no_asset_id=str(clob_ids[no_idx]),
                )
            )
    finally:
        c.close()
    return out


def calc_fee(*, fee_rate: Decimal, notional: Decimal) -> Decimal:
    # 极简：按成交额比例收费（不考虑最小费/阶梯/返利等）
    if fee_rate <= 0:
        return d("0")
    return (notional * fee_rate).quantize(d("0.00000001"))


def fmt_dec(x: Optional[Decimal], digits: int = 6) -> str:
    if x is None:
        return "NA"
    q = Decimal("1").scaleb(-digits)
    return str(x.quantize(q))


def compute_prices(
    *,
    tokens: List[MarketTokens],
    books: Dict[str, OrderBookState],
) -> Tuple[Dict[int, Dict[str, Optional[Decimal]]], Optional[Decimal]]:
    """
    Returns:
    - per_market: market_id -> {"yes_bid","yes_ask","no_bid","no_ask"}
    - sum_yes_ask (None if incomplete)
    """
    per_market: Dict[int, Dict[str, Optional[Decimal]]] = {}
    sum_yes_ask: Decimal = d("0")
    complete = True

    for t in tokens:
        yes_top = books.get(t.yes_asset_id).top if t.yes_asset_id in books else None
        no_top = books.get(t.no_asset_id).top if t.no_asset_id in books else None

        yes_bid = yes_top.best_bid if yes_top else None
        yes_ask = yes_top.best_ask if yes_top else None
        no_bid = no_top.best_bid if no_top else None
        no_ask = no_top.best_ask if no_top else None

        per_market[t.market_id] = {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
        }

        if yes_ask is None:
            complete = False
        else:
            sum_yes_ask += yes_ask

    return per_market, (sum_yes_ask if complete else None)


def safe_mid(bid: Optional[Decimal], ask: Optional[Decimal]) -> Optional[Decimal]:
    if bid is None or ask is None:
        return None
    return (bid + ask) / d("2")


async def run(args: argparse.Namespace) -> int:
    load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)

    s = load_settings()
    ws_url = args.ws_url

    tokens = fetch_market_tokens(s.gamma_base_url, args.market_ids)
    asset_ids: List[str] = []
    asset_meta: Dict[str, Dict[str, Any]] = {}  # asset_id -> {"market_id": int, "outcome": "YES"/"NO"}
    for t in tokens:
        asset_ids.extend([t.yes_asset_id, t.no_asset_id])
        asset_meta[t.yes_asset_id] = {"market_id": t.market_id, "outcome": "YES"}
        asset_meta[t.no_asset_id] = {"market_id": t.market_id, "outcome": "NO"}

    auth = load_clob_auth_from_env()

    fee_rate = d(args.fee_rate)
    qty = d(args.qty)
    threshold = d(args.threshold)

    # orderbook states by asset_id
    books: Dict[str, OrderBookState] = {}

    # paper trading state
    pos: Optional[BasketPosition] = None
    realized_pnl = d("0")

    last_print_at = utc_now()
    print_interval_s = args.print_interval_s

    # db writer (optional)
    db: Optional[PgWriter] = None
    if args.write_db:
        database_url = args.database_url or s.database_url
        db = PgWriter(database_url)
        try:
            db.connect()
        except Exception as e:
            print(
                "[DB] 连接失败："
                f"{type(e).__name__}: {e}\n"
                f"[DB] 当前 database_url={database_url}\n"
                "[DB] 解决方式（二选一）：\n"
                "  1) 启动本机 Postgres 让它监听该地址/端口；或\n"
                "  2) 把 DATABASE_URL 指向你的远端 PG（推荐写到 .env），或在命令行加 --database-url。\n"
                "     例如：--database-url 'postgresql://user:pass@<pg-host>:5432/<db>'\n",
                flush=True,
            )
            raise
    last_db_flush_at = utc_now()
    db_interval_s = args.db_interval_s

    def flush_db_prices(now: datetime) -> None:
        nonlocal last_db_flush_at
        if db is None:
            return
        if (now - last_db_flush_at).total_seconds() < db_interval_s:
            return
        last_db_flush_at = now

        # 批量写：对当前已看到的 asset_id 都做 upsert + tick
        for aid, st in books.items():
            meta = asset_meta.get(aid)
            if meta is None:
                continue
            top = st.top
            bid = top.best_bid
            ask = top.best_ask
            mid = safe_mid(bid, ask)
            raw = top.raw or {}
            try:
                db.upsert_asset_latest(
                    asset_id=aid,
                    market_id=int(meta["market_id"]),
                    outcome=str(meta["outcome"]),
                    as_of=top.as_of,
                    best_bid=bid,
                    best_ask=ask,
                    mid=mid,
                    source="clob_ws",
                    raw=raw,
                )
                if args.write_ticks:
                    db.insert_asset_tick(
                        asset_id=aid,
                        market_id=int(meta["market_id"]),
                        outcome=str(meta["outcome"]),
                        as_of=top.as_of,
                        best_bid=bid,
                        best_ask=ask,
                        mid=mid,
                        source="clob_ws",
                        raw=raw,
                    )
            except Exception:
                # 断线/PG 重启等：下次 flush 会重连再写
                try:
                    db.close()
                except Exception:
                    pass
                db.connect()

        # 同步汇总 pnl（方便你回查）
        try:
            db.upsert_paper_pnl(
                event_id=args.event_id,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl if unrealized_pnl is not None else d("0"),
            )
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            db.connect()

    while True:
        try:
            async for as_of, asset_id, ev in market_channel_stream(
                ws_url=ws_url,
                asset_ids=asset_ids,
                auth=auth,
                ping_interval_s=args.ping_interval_s,
                recv_timeout_s=max(10.0, args.ping_interval_s * 6),
            ):
                st = books.setdefault(asset_id, OrderBookState())

                kind = ev.get("kind")
                raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {"raw": ev}
                if kind == "snapshot":
                    st.apply_snapshot(ev.get("bids", []), ev.get("asks", []), as_of=as_of, raw=raw)
                elif kind == "top":
                    best_bid = ev.get("best_bid")
                    best_ask = ev.get("best_ask")
                    st.apply_top(
                        best_bid=d(best_bid) if best_bid is not None else None,
                        best_ask=d(best_ask) if best_ask is not None else None,
                        as_of=as_of,
                        raw=raw,
                    )
                elif kind == "changes":
                    st.apply_changes(ev.get("changes", []), as_of=as_of, raw=raw)
                else:
                    # unknown: try best-effort read if it contains bids/asks-like fields
                    bids = raw.get("bids")
                    asks = raw.get("asks")
                    if isinstance(bids, list) and isinstance(asks, list):
                        st.apply_snapshot(bids, asks, as_of=as_of, raw=raw)
                    # otherwise ignore

                per_market, sum_yes_ask = compute_prices(tokens=tokens, books=books)

                cond_ready = sum_yes_ask is not None
                cond_open = bool(cond_ready and sum_yes_ask < threshold)

                # open/close logic
                if pos is None and cond_open:
                    entry_yes_prices: Dict[int, Decimal] = {}
                    entry_fees: Dict[int, Decimal] = {}
                    ok = True
                    for t in tokens:
                        yes_ask = per_market[t.market_id]["yes_ask"]
                        if yes_ask is None:
                            ok = False
                            break
                        entry_yes_prices[t.market_id] = yes_ask
                        entry_fees[t.market_id] = calc_fee(fee_rate=fee_rate, notional=yes_ask * qty)
                    if ok:
                        pos = BasketPosition(
                            qty_per_leg=qty,
                            entry_yes_prices=entry_yes_prices,
                            entry_fees=entry_fees,
                            opened_at=as_of,
                        )

                        if db is not None and sum_yes_ask is not None:
                            edge = (threshold - sum_yes_ask) / threshold
                            try:
                                db.insert_arb_signal(
                                    event_id=args.event_id,
                                    as_of=as_of,
                                    kind="BUY_YES_ALL",
                                    edge=edge,
                                    detail={
                                        "threshold": str(threshold),
                                        "sum_yes_ask": str(sum_yes_ask),
                                        "markets": [t.market_id for t in tokens],
                                    },
                                )
                            except Exception:
                                try:
                                    db.close()
                                except Exception:
                                    pass
                                db.connect()

                elif pos is not None and cond_ready and not cond_open:
                    # close by selling YES at bid
                    exit_pnl = d("0")
                    exit_fee = d("0")
                    ok = True
                    for t in tokens:
                        yes_bid = per_market[t.market_id]["yes_bid"]
                        if yes_bid is None:
                            ok = False
                            break
                        buy = pos.entry_yes_prices[t.market_id]
                        exit_pnl += (yes_bid - buy) * pos.qty_per_leg
                        exit_fee += calc_fee(fee_rate=fee_rate, notional=yes_bid * pos.qty_per_leg)
                    if ok:
                        entry_fee_sum = sum(pos.entry_fees.values(), d("0"))
                        realized_pnl += exit_pnl - entry_fee_sum - exit_fee
                        pos = None

                # compute unrealized pnl (mark-to-bid) if holding
                unrealized_pnl: Optional[Decimal] = None
                if pos is not None:
                    mtm = d("0")
                    est_exit_fee = d("0")
                    ok = True
                    for t in tokens:
                        yes_bid = per_market[t.market_id]["yes_bid"]
                        if yes_bid is None:
                            ok = False
                            break
                        buy = pos.entry_yes_prices[t.market_id]
                        mtm += (yes_bid - buy) * pos.qty_per_leg
                        est_exit_fee += calc_fee(fee_rate=fee_rate, notional=yes_bid * pos.qty_per_leg)
                    if ok:
                        unrealized_pnl = mtm - sum(pos.entry_fees.values(), d("0")) - est_exit_fee

                # flush db (throttled)
                now = utc_now()
                flush_db_prices(now)

                # throttle prints
                if (now - last_print_at).total_seconds() >= print_interval_s:
                    last_print_at = now

                    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC")
                    sum_yes_s = fmt_dec(sum_yes_ask, 6) if sum_yes_ask is not None else "NA"
                    cond_s = "YES" if cond_open else ("WAIT" if not cond_ready else "NO")
                    pos_s = "OPEN" if pos is not None else "FLAT"

                    lines: List[str] = []
                    lines.append(
                        f"[{ts}] sum_yes_ask={sum_yes_s} < {fmt_dec(threshold,6)} ? {cond_s} | pos={pos_s} "
                        f"| realized={fmt_dec(realized_pnl,6)} | unrealized={fmt_dec(unrealized_pnl,6)}"
                    )
                    for t in tokens:
                        pm = per_market[t.market_id]
                        lines.append(
                            f"  m{t.market_id} YES(bid/ask)={fmt_dec(pm['yes_bid'])}/{fmt_dec(pm['yes_ask'])} "
                            f"NO(bid/ask)={fmt_dec(pm['no_bid'])}/{fmt_dec(pm['no_ask'])} | {t.question}"
                        )
                    print("\n".join(lines), flush=True)
        except Exception as e:
            # WS 断线/超时：等待后重连
            print(f"[{utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] WS error: {type(e).__name__}: {e} (reconnect...)", flush=True)
            await asyncio.sleep(args.reconnect_delay_s)
            continue

    # unreachable
    # return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--event-id", type=int, default=45883, help="仅用于打印/记录（当前逻辑按 market_ids 工作）")
    p.add_argument(
        "--market-ids",
        type=int,
        nargs="+",
        default=[601697, 601698, 601699, 601700],
        help="要订阅并做 GMP 套利检测的一组 market id",
    )
    p.add_argument("--threshold", type=float, default=1, help="开仓阈值：sum(YES ask) < threshold")
    p.add_argument("--qty", type=float, default=1.0, help="每条腿买入/卖出的份额（paper trading）")
    p.add_argument("--fee-rate", type=float, default=0.0, help="按成交额比例的手续费（极简模型）")
    p.add_argument(
        "--ws-url",
        type=str,
        default=os.getenv("CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
        help="CLOB WSS 端点（market channel：通常是 /ws/market；注意不要带末尾 /）",
    )
    p.add_argument("--ping-interval-s", type=float, default=5.0, help="发送文本 PING 的间隔秒数")
    p.add_argument("--print-interval-s", type=float, default=1.0, help="终端打印节流间隔秒数")
    p.add_argument("--reconnect-delay-s", type=float, default=3.0, help="WS 断线后的重连等待秒数")

    # PG storage (optional)
    p.add_argument("--write-db", action="store_true", help="开启：把行情/信号/PnL 写入 PG（DATABASE_URL）")
    p.add_argument(
        "--database-url",
        type=str,
        default=os.getenv("DATABASE_URL"),
        help="可选：直接指定 PG 连接串（优先于 .env/默认值），例如 postgresql://user:pass@host:5432/db",
    )
    p.add_argument("--db-interval-s", type=float, default=5.0, help="写库节流：每隔多少秒批量写一次 latest/ticks")
    p.add_argument("--write-ticks", action="store_true", help="开启：写入 asset_price_ticks（会更占空间）")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


