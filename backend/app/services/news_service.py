"""
News fetching + basic sentiment analysis.
Falls back to Google News RSS if NEWS_API_KEY is not set.
Results are cached per query for NEWS_CACHE_TTL_SECONDS (default 6 h) to
avoid redundant fetches on consecutive scans.
"""
import logging
import re
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

POSITIVE_WORDS = {
    "win", "won", "victory", "champion", "beat", "lead", "dominant",
    "undefeated", "comeback", "healthy", "active", "upgraded", "strong",
    "momentum", "streak", "confident", "record",
}
NEGATIVE_WORDS = {
    "loss", "lost", "injured", "injury", "out", "suspended", "downgraded",
    "ejected", "fired", "benched", "eliminated", "dominated", "weak",
    "struggling", "slump", "cancelled", "postponed", "forfeit",
}


def _score_text(text: str) -> float:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


class NewsService:

    def __init__(self) -> None:
        # {cache_key: (fetched_at_epoch, headlines)}
        self._cache: dict[str, tuple[float, list[str]]] = {}

    def _cache_get(self, key: str) -> Optional[list[str]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        fetched_at, headlines = entry
        if time.time() - fetched_at > settings.NEWS_CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return headlines

    def _cache_set(self, key: str, headlines: list[str]) -> None:
        self._cache[key] = (time.time(), headlines)

    async def fetch_articles(self, query: str, max_results: int = 5) -> list[str]:
        cached = self._cache_get(query)
        if cached is not None:
            logger.debug("News cache hit for %r", query)
            return cached

        if settings.NEWS_API_KEY:
            headlines = await self._newsapi_fetch(query, max_results)
        else:
            headlines = await self._rss_fallback(query, max_results)

        self._cache_set(query, headlines)
        return headlines

    async def _newsapi_fetch(self, query: str, max_results: int) -> list[str]:
        url = "https://newsapi.org/v2/everything"
        params = {"q": query, "sortBy": "publishedAt", "pageSize": max_results,
                  "language": "en", "apiKey": settings.NEWS_API_KEY}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                articles = resp.json().get("articles", [])
                return [f"{a.get('title', '')} — {a.get('description', '')}" for a in articles]
        except Exception as exc:
            logger.warning("NewsAPI error: %s", exc)
            return []

    async def _rss_fallback(self, query: str, max_results: int) -> list[str]:
        encoded = query.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()

            # Parse <item> blocks to pair each title with its description
            articles = []
            for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                title_match = re.search(r"<title>(.*?)</title>", item)
                desc_match  = re.search(r"<description>(.*?)</description>", item)
                title = title_match.group(1).strip() if title_match else ""
                desc  = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""
                if title:
                    articles.append(f"{title} — {desc}" if desc else title)
                if len(articles) >= max_results:
                    break
            return articles

        except Exception as exc:
            logger.warning("RSS fallback error: %s", exc)
            return []

    async def get_sentiment(self, market_title: str, sport: str) -> float:
        query = f"{market_title} {sport}"
        articles = await self.fetch_articles(query)
        if not articles:
            return 0.0
        return _score_text(" ".join(articles))


news_service = NewsService()
