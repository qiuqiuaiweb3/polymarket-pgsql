from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class GammaClient:
    """
    Minimal Gamma API client for metadata discovery (events/markets).

    Note: Gamma endpoints/params evolve; keep this client thin and pass params through.
    Base URL is typically https://gamma-api.polymarket.com
    """

    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s, headers={"accept": "application/json"})

    def close(self) -> None:
        self._client.close()

    @retry(wait=wait_exponential(min=0.5, max=8), stop=stop_after_attempt(5))
    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # Convenience wrappers (paths are the common ones; verify with your docs if needed)
    def list_events(self, **params: Any) -> Any:
        return self.get_json("/events", params=params)

    def get_event(self, event_id: int) -> Any:
        return self.get_json(f"/events/{event_id}")

    def list_markets(self, **params: Any) -> Any:
        return self.get_json("/markets", params=params)

    def get_market(self, market_id: int) -> Any:
        return self.get_json(f"/markets/{market_id}")


