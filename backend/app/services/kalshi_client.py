"""
Async HTTP client for the Kalshi REST API.

Authentication: RSA signature-based (not a simple bearer token).
Each request is signed with the RSA private key and identified by the key ID.

Signing algorithm:
  message  = timestamp_ms + method.upper() + path
  signature = RSA-PSS(sha256, message)  →  base64url-encoded
  Headers:  KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP
"""
import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import settings

logger = logging.getLogger(__name__)

# Maps product_metadata.competition values → our sport labels.
# Add entries here as new competitions are discovered on Kalshi.
COMPETITION_TO_SPORT: dict[str, str] = {
    # NBA
    "NBA": "NBA",
    # NFL
    "NFL": "NFL",
    # Soccer / MLS
    "MLS": "MLS",
    "Liga MX": "MLS",
    "Premier League": "MLS",
    "Bundesliga": "MLS",
    "Serie A": "MLS",
    "La Liga": "MLS",
    "Champions League": "MLS",
    "Europa League": "MLS",
    "Ligue 1": "MLS",
    "Eredivisie": "MLS",
    "England Women's Super League": "MLS",
    # Cricket
    "IPL": "Cricket",
    "T20": "Cricket",
    "T20 Match": "Cricket",
    "BBL": "Cricket",
    "PSL": "Cricket",
    "ODI": "Cricket",
    "Test Match": "Cricket",
    "CPL": "Cricket",
}

# Fallback keyword matching on text fields when product_metadata is absent
SPORT_KEYWORDS: dict[str, list[str]] = {
    "NFL": ["nfl", "super bowl", "kxmnfl"],
    "NBA": ["nba", "kxmnba"],
    "MLS": ["mls", " soccer", "liga mx", "premier league", "bundesliga", "serie a",
            "la liga", "champions league", "kxligamxgame", "kxewslgame"],
    "Cricket": ["cricket", "ipl", "t20", "kxt20match", "test match", "odi", "bbl",
                "big bash", "psl", "cpl", "wicket", "innings"],
}


class KalshiClient:
    """Async wrapper around the Kalshi trading API with RSA auth."""

    def __init__(self) -> None:
        self.base_url = settings.KALSHI_API_BASE_URL
        # The Kalshi signature covers the full URL path from the root, e.g.
        # "/trade-api/v2/portfolio/orders" — NOT just "/portfolio/orders".
        # Extract the path prefix from the base URL so we can prepend it when signing.
        # e.g. base_url = "https://api.elections.kalshi.com/trade-api/v2"
        #      → _base_path = "/trade-api/v2"
        self._base_path = urlparse(self.base_url).path.rstrip("/")

        self._private_key = None
        if settings.KALSHI_PRIVATE_KEY:
            try:
                pem = settings.KALSHI_PRIVATE_KEY.encode()
                self._private_key = serialization.load_pem_private_key(pem, password=None)
            except Exception as exc:
                logger.error("Failed to load Kalshi RSA private key: %s", exc)

    # ── Auth helpers ───────────────────────────────────────────────────────

    def _sign(self, method: str, path: str) -> dict[str, str]:
        """
        Generate RSA-PSS signed auth headers for a Kalshi API request.
        Returns an empty dict if no key is configured (anonymous read).
        """
        if not self._private_key or not settings.KALSHI_KEY_ID:
            return {}

        timestamp_ms = str(int(time.time() * 1000))
        # Sign over the full root-relative path, e.g. /trade-api/v2/portfolio/orders
        full_path = self._base_path + path
        message = (timestamp_ms + method.upper() + full_path).encode()

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY":       settings.KALSHI_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type":            "application/json",
            "Accept":                  "application/json",
        }

    def _base_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    # ── Internal HTTP ──────────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[dict] = None,
                   _retries: int = 3) -> Any:
        """GET with automatic exponential-backoff retry on 429 rate-limit errors."""
        url = f"{self.base_url}{path}"
        headers = self._sign("GET", path) or self._base_headers()
        for attempt in range(_retries):
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 429 and attempt < _retries - 1:
                    wait = 2 ** attempt          # 1s, 2s, 4s …
                    logger.warning(
                        "Rate-limited by Kalshi (429) on %s — retrying in %ds (attempt %d/%d)",
                        path, wait, attempt + 1, _retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()

    async def _post(self, path: str, body: dict) -> Any:
        url = f"{self.base_url}{path}"
        headers = self._sign("POST", path) or self._base_headers()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    # ── Public read methods ────────────────────────────────────────────────

    async def get_sports_filters(self) -> dict:
        """
        Call GET /search/filters_by_sport (public, no auth required).
        Returns available sports, competitions, and scopes on Kalshi.
        Useful for discovery and validating series prefixes at startup.
        """
        try:
            url = f"{self.base_url}/search/filters_by_sport"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            sports = list(data.get("filters_by_sports", {}).keys())
            logger.info("Kalshi available sports: %s", sports)
            return data
        except Exception as exc:
            logger.warning("Could not fetch sports filters: %s", exc)
            return {}

    async def get_markets(self, limit: int = 300) -> list[dict]:
        """
        Fetch individual game markets via the Series → Events → Markets hierarchy.

        Flow:
          1. Load all Kalshi series (paginated).
          2. Keep series whose ticker starts with a known prefix from sport_config.py.
          3. For each matching series, fetch events with nested markets in one call
             using with_nested_markets=true and mve_filter=exclude.
          4. Collect and deduplicate individual binary game markets.

        This is the only reliable way to get individual game markets on the
        production Kalshi API (GET /markets only returns KXMVE parlay wrappers).
        """
        from app.services.sport_config import SPORT_CONFIGS

        # Build a map: series_ticker_prefix (upper) → sport label
        prefix_to_sport: dict[str, str] = {}
        for sport, cfg in SPORT_CONFIGS.items():
            if sport in settings.MONITORED_SPORTS:
                for prefix in cfg["known_series_prefixes"]:
                    prefix_to_sport[prefix.upper()] = sport

        # ── Step 1: fetch all series ────────────────────────────────────────
        try:
            series_data = await self._get("/series", params={"limit": 500})
            all_series = series_data.get("series", [])
        except Exception as exc:
            logger.error("Failed to fetch series list: %s", exc)
            return []

        # ── Step 2: filter to relevant series ───────────────────────────────
        relevant: list[tuple[str, str]] = []   # (series_ticker, sport)
        for s in all_series:
            ticker = (s.get("ticker") or "").upper()
            for prefix, sport in prefix_to_sport.items():
                if ticker.startswith(prefix):
                    relevant.append((s["ticker"], sport))
                    break

        logger.info(
            "Series scan: %d total, %d match monitored sports (%s)",
            len(all_series), len(relevant),
            list(set(sp for _, sp in relevant)),
        )

        if not relevant:
            logger.warning(
                "No matching series found. Check known_series_prefixes in sport_config.py "
                "against actual series on Kalshi."
            )
            return []

        # ── Step 3: fetch events + nested markets per series ────────────────
        # Apply a per-sport cap so one sport can't crowd out another.
        # The global `limit` is a safety ceiling; actual Claude-call volume
        # is separately capped by MAX_MARKETS_PER_SCAN in the scanner.
        sport_cap = max(100, limit // max(1, len(set(sp for _, sp in relevant))))
        sport_counts: dict[str, int] = {}

        collected: list[dict] = []
        seen: set[str] = set()

        for series_ticker, sport in relevant:
            # Skip if this sport already hit its per-sport cap
            if sport_counts.get(sport, 0) >= sport_cap:
                continue

            await asyncio.sleep(0.05)   # 50 ms between calls — retry backoff handles 429s
            try:
                data = await self._get("/events", params={
                    "series_ticker":       series_ticker,
                    "status":              "open",
                    "with_nested_markets": "true",
                    "mve_filter":          "exclude",
                    "limit":               100,
                })
                events = data.get("events", [])

                event_markets = 0
                for event in events:
                    for market in event.get("markets", []):
                        ticker = market.get("ticker")
                        if not ticker or ticker in seen:
                            continue
                        # Inject series_ticker so classify_sport() has more signal
                        if not market.get("series_ticker"):
                            market["series_ticker"] = series_ticker
                        seen.add(ticker)
                        collected.append(market)
                        sport_counts[sport] = sport_counts.get(sport, 0) + 1
                        event_markets += 1
                        if len(collected) >= limit:
                            return collected

                if event_markets:
                    logger.info(
                        "Series %s [%s]: %d events, %d markets",
                        series_ticker, sport, len(events), event_markets,
                    )

            except Exception as exc:
                logger.warning("Failed to fetch events for series %s: %s", series_ticker, exc)

        logger.info(
            "get_markets complete: %d individual game markets from %d series (per-sport caps: %s)",
            len(collected), len(relevant), sport_counts,
        )
        return collected

    async def get_market(self, ticker: str) -> Optional[dict]:
        try:
            data = await self._get(f"/markets/{ticker}")
            return data.get("market")
        except Exception as exc:
            logger.warning("Failed to fetch market %s: %s", ticker, exc)
            return None

    # ── Order placement (live trading only) ───────────────────────────────

    async def place_order(
        self,
        ticker: str,
        side: str,              # "yes" or "no"
        count: int,             # number of contracts
        limit_price_cents: int, # price per contract in cents (1–99)
    ) -> Optional[dict]:
        """
        Place a limit order on Kalshi and return the order dict, or None on failure.

        Uses limit orders at the current ask price to avoid market-order slippage.
        The order is immediately-or-cancel style: if the market has moved past the
        limit price it will not fill, which is safer than chasing a bad price.

        Kalshi contract pricing:
          - Each YES contract costs  limit_price_cents  cents
          - Each NO  contract costs  (100 - limit_price_cents) cents
          - Total spend = count * limit_price_cents / 100  dollars

        Returns the 'order' sub-dict from the API response, or None on error.
        """
        if count < 1:
            logger.warning("place_order called with count=%d — skipping", count)
            return None

        body = {
            "ticker":           ticker,
            "client_order_id":  str(uuid.uuid4()),
            "type":             "limit",
            "action":           "buy",
            "side":             side,
            "count":            count,
            # For YES buys: yes_price is the limit price in cents.
            # For NO  buys: no_price  is the limit price in cents.
            f"{'yes' if side == 'yes' else 'no'}_price": limit_price_cents,
        }

        try:
            logger.info(
                "Placing live order: %s %s x%d @ %dc",
                ticker, side.upper(), count, limit_price_cents,
            )
            data = await self._post("/portfolio/orders", body)
            order = data.get("order", {})
            status = order.get("status", "unknown")
            filled = order.get("count", 0) - order.get("remaining_count", 0)
            logger.info(
                "Order %s status=%s filled=%d/%d",
                order.get("order_id", "?"), status, filled, count,
            )
            return order
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Kalshi order rejected (%s): %s — body: %s",
                exc.response.status_code, exc.response.text, body,
            )
            return None
        except Exception as exc:
            logger.error("Kalshi order failed: %s", exc)
            return None

    # ── Account balance ───────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        """
        Fetch the current balance from Kalshi's /portfolio/balance endpoint.

        Returns a dict with:
          - balance:         available cash in dollars (ready to trade)
          - portfolio_value: cash + current market value of all open positions

        Both values are None if the API call fails.
        Kalshi returns values in cents; we divide by 100.
        """
        result = {"balance": None, "portfolio_value": None}
        try:
            data = await self._get("/portfolio/balance")
            logger.debug("Kalshi /portfolio/balance raw response: %s", data)
            if "balance" in data:
                result["balance"] = int(data["balance"]) / 100.0
            if "portfolio_value" in data:
                result["portfolio_value"] = int(data["portfolio_value"]) / 100.0
            if result["balance"] is None:
                logger.warning("Kalshi /portfolio/balance: unexpected response shape: %s", data)
        except Exception as exc:
            logger.warning("Failed to fetch Kalshi balance: %s", exc)
        return result

    # ── Classification helpers ─────────────────────────────────────────────

    def classify_sport(self, market: dict) -> Optional[str]:
        # Primary: use product_metadata.competition — exact and reliable
        competition = (market.get("product_metadata") or {}).get("competition", "")
        if competition:
            sport = COMPETITION_TO_SPORT.get(competition)
            if sport and sport in settings.MONITORED_SPORTS:
                return sport

        # Fallback: keyword match across text fields
        text = " ".join([
            market.get("title", ""),
            market.get("subtitle", ""),
            market.get("ticker", ""),
            market.get("event_ticker", ""),
            market.get("series_ticker", ""),
            market.get("category", ""),
            market.get("rules_primary", ""),
        ]).lower()
        for sport, keywords in SPORT_KEYWORDS.items():
            if sport in settings.MONITORED_SPORTS:
                if any(kw in text for kw in keywords):
                    return sport
        return None

    def get_market_type(self, market: dict) -> str:
        """Infer market type from series prefix, ticker, and title.

        Types: game_winner | first_half | spread | total | other

        Detection order (most-reliable signal first):

        1. Series prefix rules:
           - *WINS series (KXNBAWINS, KXNFLWINS …) → total  (season win totals)
           - *GAME series (KXSERIEAGAME, KXLIGAMXGAME …) → game_winner
             All Kalshi soccer per-game series end with the "GAME" suffix.
           - KXNBA / KXNFL / KXSUPERBOWL with no "WINS" suffix → game_winner
             (These are per-game NBA/NFL markets, not season totals.)

        2. Ticker / title keyword checks (fallback for any unrecognised series).
        """
        ticker = (market.get("ticker") or "").upper()
        title  = (market.get("title") or "").lower()
        series = (market.get("series_ticker") or "").upper()

        # ── Series-level rules (highest confidence) ─────────────────────────
        if series:
            # Season win-total markets (e.g. KXNBAWINS-25-MEM → how many wins this season)
            if series.endswith("WINS") or "WINS" in series:
                return "total"

            # All soccer per-game series have a *GAME suffix
            if series.endswith("GAME"):
                return "game_winner"

            # KXLOSEBARCA (Barcelona-specific game markets) — also game winners
            if "LOSEBARCA" in series:
                return "game_winner"

            # NBA and NFL per-game series (already excluded *WINS above)
            if series.startswith(("KXNBA", "KXNFL", "KXSUPERBOWL")):
                return "game_winner"

            # Cricket per-game match series
            if series.startswith(("KXT20MATCH", "KXBBL", "KXPSL", "KXCPL", "KXODI", "KXTEST")):
                return "game_winner"

        # ── Ticker / title keyword fallback ──────────────────────────────────
        if "TOTAL" in ticker or "over" in title or "under" in title:
            return "total"
        if "SPREAD" in ticker or "wins by" in title or "covers" in title:
            return "spread"
        if "1H" in ticker or "first half" in title or "1st half" in title:
            return "first_half"
        if ("WINNER" in ticker or " wins" in title or "beats" in title
                or "win the" in title or "who wins" in title):
            return "game_winner"

        return "other"

    def extract_best_price(self, market: dict) -> tuple[float, float]:
        """Return (yes_bid, yes_ask) as probabilities in [0, 1].

        The Kalshi API returns prices as dollar strings (e.g. "0.6500").
        Use yes_bid_dollars/yes_ask_dollars (current live prices).
        Fall back to previous_* only if current prices are zero.
        """
        def _parse(val) -> float:
            try:
                return float(val or 0)
            except (TypeError, ValueError):
                return 0.0

        yes_bid = _parse(market.get("yes_bid_dollars")) or _parse(market.get("previous_yes_bid_dollars"))
        yes_ask = _parse(market.get("yes_ask_dollars")) or _parse(market.get("previous_yes_ask_dollars"))
        return yes_bid, yes_ask


kalshi_client = KalshiClient()
