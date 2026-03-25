"""
News fetching + basic sentiment analysis.

Priority:
  1. NewsAPI.org (if NEWS_API_KEY is set)
  2. Fallback: web-search via a public RSS/scraping heuristic (no key needed)

Sentiment is scored -1.0 (very negative) … +1.0 (very positive) using a
simple keyword-weighted approach so we avoid heavyweight NLP dependencies.
"""
import logging
import re
from typing import Optional

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

# ── Keyword sentiment lexicon ──────────────────────────────────────────────
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
    """Simple keyword-frequency sentiment score in [-1, 1]."""
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


class NewsService:
    """Fetches and scores news for a given sports query."""

    async def fetch_articles(self, query: str, max_results: int = 5) -> list[str]:
        """Return a list of article headlines/snippets for the query."""
        if settings.NEWS_API_KEY:
            return await self._newsapi_fetch(query, max_results)
        return await self._rss_fallback(query, max_results)

    async def _newsapi_fetch(self, query: str, max_results: int) -> list[str]:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "sortBy": "publishedAt",
            "pageSize": max_results,
            "language": "en",
            "apiKey": settings.NEWS_API_KEY,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                articles = resp.json().get("articles", [])
                return [
                    f"{a.get('title', '')} — {a.get('description', '')}"
                    for a in articles
                ]
        except Exception as exc:
            logger.warning("NewsAPI error: %s", exc)
            return []

    async def _rss_fallback(self, query: str, max_results: int) -> list[str]:
        """
        Lightweight fallback: fetch Google News RSS for the query.
        Returns plain-text snippets extracted from <title> tags.
        """
        encoded = query.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                # Quick XML <title> extraction — avoids an xml parser dependency
                titles = re.findall(r"<title>(?!Google News)(.*?)</title>", resp.text)
                return titles[:max_results]
        except Exception as exc:
            logger.warning("RSS fallback error: %s", exc)
            return []

    async def get_sentiment(self, market_title: str, sport: str) -> float:
        """
        Return a sentiment score in [-1, 1] for the given market/sport context.
        """
        query = f"{market_title} {sport}"
        articles = await self.fetch_articles(query)
        if not articles:
            return 0.0
        combined = " ".join(articles)
        score = _score_text(combined)
        logger.debug("Sentiment for '%s': %.3f", market_title[:60], score)
        return score


news_service = NewsService()
