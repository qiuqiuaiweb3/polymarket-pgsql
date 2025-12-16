from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import websockets


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(x: Any) -> Optional[Decimal]:
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except Exception:
        return None


def _parse_level(level: Any) -> Optional[Tuple[Decimal, Decimal]]:
    """
    Try to parse a single book level into (price, size).

    Docs/clients have been seen to emit levels as:
    - ["0.12", "100"]
    - {"price": "0.12", "size": "100"}
    - {"price": "0.12", "quantity": "100"}
    """
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        p = _to_decimal(level[0])
        s = _to_decimal(level[1])
        if p is None or s is None:
            return None
        return p, s

    if isinstance(level, Mapping):
        p = _to_decimal(level.get("price"))
        s = _to_decimal(level.get("size"))
        if s is None:
            s = _to_decimal(level.get("quantity"))
        if p is None or s is None:
            return None
        return p, s

    return None


def _best_from_levels(levels: Iterable[Any], *, side: str) -> Optional[Decimal]:
    best: Optional[Decimal] = None
    for lvl in levels:
        parsed = _parse_level(lvl)
        if parsed is None:
            continue
        price, size = parsed
        if size <= 0:
            continue
        if best is None:
            best = price
            continue
        if side == "bid" and price > best:
            best = price
        if side == "ask" and price < best:
            best = price
    return best


@dataclass
class OrderBookTop:
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    as_of: datetime = field(default_factory=utc_now)
    raw: Optional[Dict[str, Any]] = None

    @property
    def mid(self) -> Optional[Decimal]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal("2")


@dataclass
class OrderBookState:
    bids: Dict[Decimal, Decimal] = field(default_factory=dict)  # price -> size
    asks: Dict[Decimal, Decimal] = field(default_factory=dict)  # price -> size
    top: OrderBookTop = field(default_factory=OrderBookTop)

    def _recompute_top(self, *, as_of: datetime, raw: Optional[Dict[str, Any]] = None) -> None:
        best_bid = max((p for p, s in self.bids.items() if s > 0), default=None)
        best_ask = min((p for p, s in self.asks.items() if s > 0), default=None)
        self.top = OrderBookTop(best_bid=best_bid, best_ask=best_ask, as_of=as_of, raw=raw)

    def apply_snapshot(self, bids: Iterable[Any], asks: Iterable[Any], *, as_of: datetime, raw: Dict[str, Any]) -> None:
        self.bids.clear()
        self.asks.clear()
        for lvl in bids:
            parsed = _parse_level(lvl)
            if parsed is None:
                continue
            p, s = parsed
            self.bids[p] = s
        for lvl in asks:
            parsed = _parse_level(lvl)
            if parsed is None:
                continue
            p, s = parsed
            self.asks[p] = s
        self._recompute_top(as_of=as_of, raw=raw)

    def apply_changes(self, changes: Iterable[Any], *, as_of: datetime, raw: Dict[str, Any]) -> None:
        """
        Apply incremental updates if server emits 'changes' style messages.

        Observed patterns in similar CLOB feeds:
        - ["buy", "0.12", "100"]   (bid)
        - ["sell", "0.13", "0"]   (ask delete)
        - {"side":"buy","price":"0.12","size":"100"}
        """

        for ch in changes:
            side: Optional[str] = None
            price: Optional[Decimal] = None
            size: Optional[Decimal] = None

            if isinstance(ch, (list, tuple)) and len(ch) >= 3:
                side = str(ch[0]).lower()
                price = _to_decimal(ch[1])
                size = _to_decimal(ch[2])
            elif isinstance(ch, Mapping):
                side = str(ch.get("side") or ch.get("type") or "").lower()
                price = _to_decimal(ch.get("price"))
                size = _to_decimal(ch.get("size") if "size" in ch else ch.get("quantity"))

            if side not in {"buy", "sell", "bid", "ask"}:
                continue
            if price is None or size is None:
                continue

            if side in {"buy", "bid"}:
                if size <= 0:
                    self.bids.pop(price, None)
                else:
                    self.bids[price] = size
            else:
                if size <= 0:
                    self.asks.pop(price, None)
                else:
                    self.asks[price] = size

        self._recompute_top(as_of=as_of, raw=raw)

    def apply_top(
        self,
        *,
        best_bid: Optional[Decimal],
        best_ask: Optional[Decimal],
        as_of: datetime,
        raw: Dict[str, Any],
    ) -> None:
        # 不强制更新 bids/asks 全量深度；仅维护 top-of-book
        self.top = OrderBookTop(best_bid=best_bid, best_ask=best_ask, as_of=as_of, raw=raw)


def extract_asset_id(msg: Mapping[str, Any]) -> Optional[str]:
    for k in ("asset_id", "assetId", "token_id", "tokenId"):
        v = msg.get(k)
        if v is not None:
            return str(v)
    return None


def parse_market_channel_message(msg: Mapping[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Normalize a WS payload for the market channel into (asset_id, normalized_event).

    normalized_event is one of:
    - {"kind":"snapshot","bids":[...],"asks":[...], "raw": msg}
    - {"kind":"changes","changes":[...], "raw": msg}
    """
    asset_id = extract_asset_id(msg)
    if asset_id is None:
        return None, None

    # full book snapshot style
    if "bids" in msg and "asks" in msg and isinstance(msg.get("bids"), list) and isinstance(msg.get("asks"), list):
        return asset_id, {"kind": "snapshot", "bids": msg["bids"], "asks": msg["asks"], "raw": dict(msg)}

    # top-of-book style
    if "best_bid" in msg or "best_ask" in msg:
        return asset_id, {"kind": "top", "best_bid": msg.get("best_bid"), "best_ask": msg.get("best_ask"), "raw": dict(msg)}

    # delta style
    if "changes" in msg and isinstance(msg.get("changes"), list):
        return asset_id, {"kind": "changes", "changes": msg["changes"], "raw": dict(msg)}

    return asset_id, {"kind": "unknown", "raw": dict(msg)}


async def market_channel_stream(
    *,
    ws_url: str,
    asset_ids: List[str],
    auth: Optional[Dict[str, str]] = None,
    ping_interval_s: float = 5.0,
    recv_timeout_s: float = 60.0,
) -> Iterable[Tuple[datetime, str, Dict[str, Any]]]:
    """
    Connect to Polymarket CLOB market channel and yield normalized events.

    Note: This is an async generator.
    """
    subscribe_msg: Dict[str, Any] = {"assets_ids": asset_ids, "type": "market"}
    if auth:
        subscribe_msg["auth"] = auth

    async with websockets.connect(ws_url, ping_interval=None) as ws:
        await ws.send(json.dumps(subscribe_msg))

        async def _ping_loop() -> None:
            while True:
                try:
                    await ws.send("PING")
                except Exception:
                    return
                await asyncio.sleep(ping_interval_s)

        ping_task = asyncio.create_task(_ping_loop())
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout_s)
                as_of = utc_now()

                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue

                if not isinstance(raw, str):
                    continue

                if raw in {"PONG", "PING"}:
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:
                    # non-json keepalives or unexpected payloads
                    continue

                def _yield_one(m: Any) -> Iterable[Tuple[datetime, str, Dict[str, Any]]]:
                    if not isinstance(m, dict):
                        return []

                    # Some events batch multiple per message: {"event_type":"price_change","price_changes":[...]}
                    if isinstance(m.get("price_changes"), list):
                        out: List[Tuple[datetime, str, Dict[str, Any]]] = []
                        for pc in m["price_changes"]:
                            if not isinstance(pc, dict):
                                continue
                            merged = dict(pc)
                            # keep some context
                            if "timestamp" in m and "timestamp" not in merged:
                                merged["timestamp"] = m["timestamp"]
                            if "market" in m and "market" not in merged:
                                merged["market"] = m["market"]
                            if "event_type" in m and "event_type" not in merged:
                                merged["event_type"] = m["event_type"]
                            asset_id, norm = parse_market_channel_message(merged)
                            if asset_id is None or norm is None:
                                continue
                            out.append((as_of, asset_id, norm))
                        return out

                    asset_id, norm = parse_market_channel_message(m)
                    if asset_id is None or norm is None:
                        return []
                    return [(as_of, asset_id, norm)]

                if isinstance(msg, list):
                    for item in msg:
                        for tup in _yield_one(item):
                            yield tup
                else:
                    for tup in _yield_one(msg):
                        yield tup
        finally:
            ping_task.cancel()


