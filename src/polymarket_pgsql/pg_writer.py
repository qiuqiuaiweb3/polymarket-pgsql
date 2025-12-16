from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import psycopg
from psycopg.types.json import Jsonb


@dataclass
class PgWriter:
    database_url: str
    conn: Optional[psycopg.Connection[Any]] = None

    def connect(self) -> None:
        if self.conn is not None and not self.conn.closed:
            return
        self.conn = psycopg.connect(self.database_url, autocommit=True)

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def _ensure(self) -> psycopg.Connection[Any]:
        self.connect()
        assert self.conn is not None
        return self.conn

    def upsert_asset_latest(
        self,
        *,
        asset_id: str,
        market_id: int,
        outcome: str,
        as_of: datetime,
        best_bid: Optional[Decimal],
        best_ask: Optional[Decimal],
        mid: Optional[Decimal],
        source: str,
        raw: Dict[str, Any],
    ) -> None:
        conn = self._ensure()
        conn.execute(
            """
            insert into asset_price_latest (
              asset_id, market_id, outcome, as_of, best_bid, best_ask, mid, source, raw
            ) values (
              %(asset_id)s, %(market_id)s, %(outcome)s, %(as_of)s, %(best_bid)s, %(best_ask)s, %(mid)s, %(source)s, %(raw)s
            )
            on conflict (asset_id) do update set
              market_id = excluded.market_id,
              outcome = excluded.outcome,
              as_of = excluded.as_of,
              best_bid = excluded.best_bid,
              best_ask = excluded.best_ask,
              mid = excluded.mid,
              source = excluded.source,
              raw = excluded.raw,
              updated_at = now()
            """,
            {
                "asset_id": asset_id,
                "market_id": market_id,
                "outcome": outcome,
                "as_of": as_of,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "source": source,
                "raw": Jsonb(raw),
            },
        )

    def insert_asset_tick(
        self,
        *,
        asset_id: str,
        market_id: int,
        outcome: str,
        as_of: datetime,
        best_bid: Optional[Decimal],
        best_ask: Optional[Decimal],
        mid: Optional[Decimal],
        source: str,
        raw: Dict[str, Any],
    ) -> None:
        conn = self._ensure()
        conn.execute(
            """
            insert into asset_price_ticks (
              asset_id, as_of, market_id, outcome, best_bid, best_ask, mid, source, raw
            ) values (
              %(asset_id)s, %(as_of)s, %(market_id)s, %(outcome)s, %(best_bid)s, %(best_ask)s, %(mid)s, %(source)s, %(raw)s
            )
            on conflict (asset_id, as_of) do nothing
            """,
            {
                "asset_id": asset_id,
                "as_of": as_of,
                "market_id": market_id,
                "outcome": outcome,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "source": source,
                "raw": Jsonb(raw),
            },
        )

    def insert_arb_signal(
        self,
        *,
        event_id: int,
        as_of: datetime,
        kind: str,
        edge: Decimal,
        detail: Dict[str, Any],
    ) -> None:
        conn = self._ensure()
        conn.execute(
            """
            insert into arb_signals (event_id, as_of, kind, edge, detail)
            values (%(event_id)s, %(as_of)s, %(kind)s, %(edge)s, %(detail)s)
            """,
            {"event_id": event_id, "as_of": as_of, "kind": kind, "edge": edge, "detail": Jsonb(detail)},
        )

    def upsert_paper_pnl(
        self,
        *,
        event_id: int,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
    ) -> None:
        conn = self._ensure()
        conn.execute(
            """
            insert into paper_pnl (event_id, realized_pnl, unrealized_pnl)
            values (%(event_id)s, %(realized)s, %(unrealized)s)
            on conflict (event_id) do update set
              realized_pnl = excluded.realized_pnl,
              unrealized_pnl = excluded.unrealized_pnl,
              updated_at = now()
            """,
            {"event_id": event_id, "realized": realized_pnl, "unrealized": unrealized_pnl},
        )




