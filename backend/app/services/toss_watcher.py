"""
Toss Watcher — polls ESPNcricinfo RSS every 10 minutes looking for toss results.

When a toss headline is detected for a team pair that matches an open Kalshi
cricket market, a targeted mini-scan is triggered immediately for that market.
The normal MARKET_MIN_HOURS_AHEAD filter is relaxed to 10 minutes for these
toss-triggered scans — games are typically 20–30 min away at toss time, but
we still have time to place a trade.

Toss result is the single strongest T20 predictor (~5–8% probability shift),
so catching it promptly is the highest-value real-time signal available.

Log lines emitted:
  TossWatcher: found 1 new toss headline(s)
  TossWatcher: 'KKR won the toss ...' → matched KXIPLGAME-... (triggering scan)
  TossWatcher: no matching Kalshi market for '...'
"""
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ESPNCRICINFO_RSS_URL = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"

# Keywords that indicate a toss result headline
_TOSS_PHRASES = ("won the toss", "win the toss", "wins the toss")

# How long to suppress re-triggering on the same headline fingerprint
_SEEN_TTL = timedelta(hours=8)

# Short team abbreviations and keywords used for matching headlines → Kalshi tickers
# These are the tokens we look for in both the headline and the market title.
_STOPWORDS = {
    "the", "and", "to", "of", "in", "at", "vs", "for", "won", "win", "wins",
    "toss", "elected", "elect", "chose", "choose", "opt", "opted",
    "bat", "bowl", "field", "first", "after", "against", "with", "are",
    "will", "has", "have",
}


class TossWatcher:
    """Polls ESPNcricinfo RSS and fires targeted mini-scans on toss detection."""

    def __init__(self) -> None:
        # fingerprint → first-seen datetime; entries expire after _SEEN_TTL
        self._seen: dict[str, datetime] = {}

    # ── Deduplication ──────────────────────────────────────────────────────────

    def _key(self, text: str) -> str:
        return text[:80].lower().strip()

    def _has_seen(self, key: str) -> bool:
        seen_at = self._seen.get(key)
        if seen_at is None:
            return False
        if datetime.now(timezone.utc) - seen_at > _SEEN_TTL:
            del self._seen[key]
            return False
        return True

    def _mark_seen(self, key: str) -> None:
        self._seen[key] = datetime.now(timezone.utc)

    # ── Parsing helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _is_toss_headline(text: str) -> bool:
        t = text.lower()
        return any(p in t for p in _TOSS_PHRASES)

    @staticmethod
    def _extract_tokens(headline: str) -> set[str]:
        """
        Extract short meaningful tokens from the headline for fuzzy market matching.

        Strategy:
        - Keep all-caps tokens ≥ 2 chars (KKR, MI, SRH, CSK, RCB, etc.)
        - Keep title-case or lower tokens ≥ 4 chars that aren't stopwords
          (Mumbai, Chennai, Kolkata, Delhi, Rajasthan, etc.)
        - Drop pure stopwords and short noise tokens.
        """
        tokens: set[str] = set()
        for word in re.findall(r"[A-Za-z]+", headline):
            w_lower = word.lower()
            if w_lower in _STOPWORDS:
                continue
            if len(word) >= 2 and word.isupper():
                tokens.add(w_lower)       # KKR, MI, RCB ...
            elif len(word) >= 4:
                tokens.add(w_lower)       # Mumbai, Chennai, Kolkata ...
        return tokens

    @staticmethod
    def _market_matches(tokens: set[str], market: dict) -> bool:
        """Return True if any token appears in the market title or ticker."""
        haystack = (
            (market.get("title") or "") + " " + (market.get("ticker") or "")
        ).lower()
        return any(t in haystack for t in tokens)

    # ── RSS fetch ──────────────────────────────────────────────────────────────

    async def _fetch_new_toss_headlines(self) -> list[str]:
        """Pull ESPNcricinfo RSS; return toss headlines not seen before."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(ESPNCRICINFO_RSS_URL, follow_redirects=True)
                resp.raise_for_status()
        except Exception as exc:
            logger.debug("TossWatcher RSS fetch failed: %s", exc)
            return []

        new: list[str] = []
        for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
            m = re.search(r"<title>(.*?)</title>", item)
            if not m:
                continue
            title = m.group(1).strip()
            if not self._is_toss_headline(title):
                continue
            key = self._key(title)
            if not self._has_seen(key):
                new.append(title)
        return new

    # ── Main polling method (called by APScheduler every 10 min) ─────────────

    async def check_and_trigger(self) -> None:
        headlines = await self._fetch_new_toss_headlines()
        if not headlines:
            return

        logger.info("TossWatcher: %d new toss headline(s) detected", len(headlines))

        # Fetch open cricket markets once for this poll cycle
        from app.services.kalshi_client import kalshi_client  # avoid circular at import
        try:
            all_markets = await kalshi_client.get_markets(limit=500)
        except Exception as exc:
            logger.warning("TossWatcher: could not fetch Kalshi markets: %s", exc)
            return

        cricket_markets = [
            m for m in all_markets
            if kalshi_client.classify_sport(m) == "Cricket"
            and m.get("status") == "open"
        ]

        if not cricket_markets:
            logger.debug("TossWatcher: no open cricket markets to match against")
            for hl in headlines:
                self._mark_seen(self._key(hl))
            return

        from app.services.scanner import scanner  # avoid circular at import

        for headline in headlines:
            key    = self._key(headline)
            tokens = self._extract_tokens(headline)
            matched = [
                m for m in cricket_markets
                if self._market_matches(tokens, m)
            ]

            self._mark_seen(key)   # always mark so we don't re-log on next poll

            if matched:
                tickers = [m.get("ticker", "?") for m in matched]
                logger.info(
                    "TossWatcher: '%s' → matched %d market(s) %s — triggering mini-scan",
                    headline[:80], len(matched), tickers,
                )
                # Fire the targeted scan as a background task so the scheduler
                # job returns immediately and doesn't block the next poll.
                asyncio.create_task(
                    scanner.run_toss_triggered(matched, toss_headline=headline)
                )
            else:
                logger.debug(
                    "TossWatcher: no Kalshi cricket market matched headline: '%s'",
                    headline[:80],
                )


toss_watcher = TossWatcher()
