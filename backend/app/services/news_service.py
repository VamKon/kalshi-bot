"""
News fetching + basic sentiment analysis.
Falls back to Google News RSS if NEWS_API_KEY is not set.
Results are cached per query for NEWS_CACHE_TTL_SECONDS (default 6 h) to
avoid redundant fetches on consecutive scans.

For Cricket markets three sources are fetched in parallel and merged:
  1. CricBuzz RSS      — fastest for toss results, playing XI, live updates
  2. ESPNcricinfo RSS  — authoritative editorial, match previews, injury news
  3. Google News RSS   — broadest coverage, catches regional/niche sources

Each RSS feed is cached as a single raw-feed blob (not per query), so all
three cricket queries per market (base / toss / squad) share one network call
per feed per 6-hour TTL window.
"""
import asyncio
import logging
import re
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# CricBuzz cricket news RSS — fast updates, toss results, playing XI, live scores
CRICBUZZ_RSS_URL = "https://www.cricbuzz.com/cricket-news/rss-feed"

# ESPNcricinfo general cricket news RSS — no API key needed, updated frequently
ESPNCRICINFO_RSS_URL = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"

# Words to strip when building keyword sets for ESPNcricinfo article matching
_QUERY_STOPWORDS = {
    "the", "and", "for", "vs", "2026", "today", "prediction",
    "cricket", "match", "game", "win", "will", "who",
}

POSITIVE_WORDS = {
    "win", "won", "victory", "champion", "beat", "lead", "dominant",
    "undefeated", "comeback", "healthy", "active", "upgraded", "strong",
    "momentum", "streak", "confident", "record",
    # Cricket-specific positives
    "century", "centuries", "wickets", "allrounder", "captains", "form",
    "batting", "bowling", "powerplay", "chase", "defended", "unbeaten",
    "qualified", "finals", "playoff",
}
NEGATIVE_WORDS = {
    "loss", "lost", "injured", "injury", "out", "suspended", "downgraded",
    "ejected", "fired", "benched", "eliminated", "dominated", "weak",
    "struggling", "slump", "cancelled", "postponed", "forfeit",
    # Cricket-specific negatives
    "rain", "abandoned", "rested", "unavailable", "doubtful", "ruled out",
    "concussion", "hamstring", "dns", "dnb", "dropped", "excluded",
    "rain delay", "wet outfield", "no result",
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

    async def fetch_articles(
        self, query: str, max_results: int = 5, sport: str = ""
    ) -> list[str]:
        """
        Fetch news headlines for *query*.

        When sport="Cricket" the method also fetches ESPNcricinfo's RSS feed
        in parallel and merges results, giving more authoritative cricket
        coverage (toss results, playing XI, injury news) than Google News alone.
        """
        cache_key = f"{sport}:{query}" if sport == "Cricket" else query
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("News cache hit for %r", cache_key)
            return cached

        if settings.NEWS_API_KEY:
            primary_task = self._newsapi_fetch(query, max_results)
        else:
            primary_task = self._rss_fallback(query, max_results)

        if sport == "Cricket":
            # Fetch all three sources in parallel
            primary_headlines, cricbuzz_headlines, espn_headlines = await asyncio.gather(
                primary_task,
                self._cricbuzz_fetch(query, max_results),
                self._espncricinfo_fetch(query, max_results),
            )
            # Merge — CricBuzz first (fastest for live updates/toss),
            # then ESPNcricinfo (authoritative editorial), then Google News
            seen: set[str] = set()
            headlines: list[str] = []
            for h in cricbuzz_headlines + espn_headlines + primary_headlines:
                key = h[:60]
                if key not in seen:
                    seen.add(key)
                    headlines.append(h)
        else:
            headlines = await primary_task

        self._cache_set(cache_key, headlines)
        return headlines

    async def _cricbuzz_fetch(self, query: str, max_results: int) -> list[str]:
        """
        Pull CricBuzz's RSS feed and return articles that mention any keyword
        from *query*.

        The raw feed is cached independently under '_cricbuzz_raw_feed' so all
        cricket queries in the same scan cycle share one network call.
        CricBuzz is the fastest source for toss results, playing XI, and live
        score updates.
        """
        feed_cache_key = "_cricbuzz_raw_feed"
        all_articles: list[str] | None = self._cache_get(feed_cache_key)

        if all_articles is None:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        CRICBUZZ_RSS_URL, follow_redirects=True
                    )
                    resp.raise_for_status()

                all_articles = []
                for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                    title_m = re.search(r"<title>(.*?)</title>", item)
                    desc_m  = re.search(r"<description>(.*?)</description>", item)
                    title = title_m.group(1).strip() if title_m else ""
                    desc  = (
                        re.sub(r"<[^>]+>", "", desc_m.group(1)).strip()
                        if desc_m else ""
                    )
                    if title:
                        all_articles.append(
                            f"{title} — {desc}" if desc else title
                        )

                self._cache_set(feed_cache_key, all_articles)
                logger.info(
                    "CricBuzz RSS: fetched %d articles", len(all_articles)
                )
            except Exception as exc:
                logger.warning("CricBuzz RSS error: %s", exc)
                return []

        # Build keyword set from the query — strip stopwords and short tokens
        keywords = {
            w.lower()
            for w in re.findall(r"\b\w{3,}\b", query)
            if w.lower() not in _QUERY_STOPWORDS
        }

        matched: list[str] = []
        for article in all_articles:
            article_lower = article.lower()
            if any(kw in article_lower for kw in keywords):
                matched.append(article)
                if len(matched) >= max_results:
                    break

        logger.debug(
            "CricBuzz: %d/%d articles match query %r",
            len(matched), len(all_articles), query,
        )
        return matched

    async def _espncricinfo_fetch(self, query: str, max_results: int) -> list[str]:
        """
        Pull ESPNcricinfo's general RSS feed and return articles that mention
        any keyword from *query*.

        The raw feed (typically 20-30 articles) is cached independently under
        a dedicated key so that multiple cricket queries in the same scan cycle
        only hit ESPNcricinfo once. Each query then filters from the cached feed
        in-memory, so no extra network calls are made.
        """
        feed_cache_key = "_espncricinfo_raw_feed"
        all_articles: list[str] | None = self._cache_get(feed_cache_key)

        if all_articles is None:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        ESPNCRICINFO_RSS_URL, follow_redirects=True
                    )
                    resp.raise_for_status()

                all_articles = []
                for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                    title_m = re.search(r"<title>(.*?)</title>", item)
                    desc_m  = re.search(r"<description>(.*?)</description>", item)
                    title = title_m.group(1).strip() if title_m else ""
                    desc  = (
                        re.sub(r"<[^>]+>", "", desc_m.group(1)).strip()
                        if desc_m else ""
                    )
                    if title:
                        all_articles.append(
                            f"{title} — {desc}" if desc else title
                        )

                self._cache_set(feed_cache_key, all_articles)
                logger.info(
                    "ESPNcricinfo RSS: fetched %d articles", len(all_articles)
                )
            except Exception as exc:
                logger.warning("ESPNcricinfo RSS error: %s", exc)
                return []

        # Build keyword set from the query — strip stopwords and short tokens
        keywords = {
            w.lower()
            for w in re.findall(r"\b\w{3,}\b", query)
            if w.lower() not in _QUERY_STOPWORDS
        }

        matched: list[str] = []
        for article in all_articles:
            article_lower = article.lower()
            if any(kw in article_lower for kw in keywords):
                matched.append(article)
                if len(matched) >= max_results:
                    break

        logger.debug(
            "ESPNcricinfo: %d/%d articles match query %r",
            len(matched), len(all_articles), query,
        )
        return matched

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
