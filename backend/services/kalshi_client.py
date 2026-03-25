"""
Async HTTP client for the Kalshi demo/sandbox REST API.

Docs: https://trading-api.readme.io/reference
"""
import logging
from typing import Any, Optional

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Sports keywords to help filter markets by sport
SPORT_KEYWORDS: dict[str, list[str]] = {
    "NFL": ["nfl", "super bowl", "football", "touchdown", "nfc", "afc"],
    "NBA": ["nba", "basketball", "nba finals", "playoff"],
    "MLS": ["mls", "soccer", "mls cup", "major league soccer"],
    "IPL": ["ipl", "cricket", "indian premier league", "t20"],
}


class KalshiClient:
    """Thin async wrapper around the Kalshi trading API."""

    def __init__(self) -> None:
        self.base_url = settings.KALSHI_API_BASE_URL
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if settings.KALSHI_API_KEY:
            self._headers["Authorization"] = f"Bearer {settings.KALSHI_API_KEY}"

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()

    # ── Public methods ─────────────────────────────────────────────────────

    async def get_markets(
        self,
        status: str = "open",
        limit: int = 200,
    ) -> list[dict]:
        """Return a list of markets from the Kalshi API."""
        try:
            data = await self._get(
                "/markets",
                params={"status": status, "limit": limit},
            )
            return data.get("markets", [])
        except Exception as exc:
            logger.error("Failed to fetch markets: %s", exc)
            return []

    async def get_market(self, ticker: str) -> Optional[dict]:
        """Return details for a single market."""
        try:
            data = await self._get(f"/markets/{ticker}")
            return data.get("market")
        except Exception as exc:
            logger.warning("Failed to fetch market %s: %s", ticker, exc)
            return None

    def classify_sport(self, market: dict) -> Optional[str]:
        """
        Attempt to classify a Kalshi market into one of the monitored sports
        by checking the title and series_ticker against known keywords.
        Returns the sport string or None if no match.
        """
        text = " ".join([
            market.get("title", ""),
            market.get("subtitle", ""),
            market.get("series_ticker", ""),
            market.get("category", ""),
        ]).lower()

        for sport, keywords in SPORT_KEYWORDS.items():
            if sport in settings.MONITORED_SPORTS:
                if any(kw in text for kw in keywords):
                    return sport
        return None

    def extract_best_price(self, market: dict) -> tuple[float, float]:
        """
        Return (yes_bid, yes_ask) as probabilities (0–1).
        Kalshi prices are in cents (0–100), so divide by 100.
        """
        yes_bid = (market.get("yes_bid") or 0) / 100
        yes_ask = (market.get("yes_ask") or 0) / 100
        return yes_bid, yes_ask


kalshi_client = KalshiClient()
