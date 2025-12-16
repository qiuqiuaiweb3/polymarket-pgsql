#!/usr/bin/env python3
"""
Quick smoke test for Gamma connectivity.

It simply calls /markets with a small page size and prints the first item keys.
You can expand it into your incremental sync later.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from polymarket_pgsql.config import load_settings
from polymarket_pgsql.gamma_client import GammaClient


def main() -> int:
    # load .env if present (user will create it from env.example)
    load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", ".env"), override=False)

    s = load_settings()
    c = GammaClient(s.gamma_base_url)
    try:
        data = c.list_markets(limit=1)
        print("Gamma /markets result type:", type(data))
        if isinstance(data, list) and data:
            print("First market keys:", sorted(list(data[0].keys()))[:30])
        else:
            print("Response (truncated):", str(data)[:500])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


