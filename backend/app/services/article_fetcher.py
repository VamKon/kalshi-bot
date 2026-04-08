"""
Fetch full cricket articles for OpenRouter fact extraction.

Two sources used to discover article URLs:
  1. Google News RSS search  — match-specific query, capped at 10 URLs
  2. ESPNcricinfo RSS        — global feed cached once, keyword-filtered for URLs only

ESPNcricinfo article PAGES are NOT fetched — they return 403 Forbidden.
The ESPN RSS is only used to collect candidate URLs that are skipped during
the full-text fetch phase (only Google News article pages are fetched).

Caching:
  - Google News RSS results: per-query, in-memory, 2-hour TTL
  - ESPNcricinfo RSS feed:   singleton, cached for process lifetime

Article fetch strategy: sequential with early exit — fetch one URL at a time,
stop as soon as max_articles successful full-text fetches are collected.
This avoids firing all HTTP requests simultaneously and getting rate-limited.

Returns list of {"text": str, "url": str, "title": str} dicts.
Failures are caught and logged — callers always receive a (possibly empty) list.
"""

import logging
import re
import time
import urllib.parse
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Max characters per article sent to OpenRouter
MAX_ARTICLE_CHARS = 10_000

# How long to cache a Google News RSS query result (seconds)
GOOGLE_NEWS_CACHE_TTL = 2 * 60 * 60  # 2 hours

ESPNCRICINFO_RSS_URL = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"

# Max Google News URLs to collect per query (avoids fetching 100 candidate URLs)
GOOGLE_NEWS_MAX_URLS = 10

# ESPNcricinfo article pages always return 403 — skip fetching them.
# Only use ESPN RSS for keyword matching; let Cricbuzz / Google News supply text.
_SKIP_FETCH_DOMAINS = {"espncricinfo.com"}

# Words ignored when building keyword filter sets
_STOPWORDS = {
    "the", "and", "for", "vs", "2025", "2026", "today", "prediction",
    "cricket", "match", "game", "win", "will", "who",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _keywords(query: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"\b\w{3,}\b", query)
        if w.lower() not in _STOPWORDS
    }


class ArticleFetcher:
    """Fetch full cricket article text for OpenRouter fact extraction."""

    def __init__(self) -> None:
        # Per-query Google News cache: {query: (fetched_at_epoch, [urls])}
        self._gnews_cache: dict[str, tuple[float, list[str]]] = {}

        # Global RSS feed caches (fetched once per process lifetime)
        self._espn_raw: list[dict] | None = None   # [{title, url, description}]

    # ── Public entry point ─────────────────────────────────────────────────────

    async def fetch_match_articles(
        self,
        home_team: str,
        away_team: str,
        competition: str = "",
        max_articles: int = 5,
    ) -> list[dict]:
        """
        Collect up to max_articles full-text articles about the match.

        Sources:
          - ESPNcricinfo: RSS title+description used directly as mini-articles
            (article pages block bots with 403; RSS summaries are free data).
          - Google News:  search RSS → follow redirect URLs to full article text.
            Capped at GOOGLE_NEWS_MAX_URLS (10) candidates to avoid excessive fetching.

        Article fetch is sequential with early exit to avoid rate limits.
        Returns list of {"text", "url", "title"}.
        """
        query = f"{home_team} vs {away_team}"
        if competition:
            query += f" {competition}"
        kw = _keywords(query)

        async with httpx.AsyncClient(
            timeout=15.0,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # ── Step 1: ESPN mini-articles from RSS descriptions (no page fetch) ─
            espn_articles = await self._espn_rss_articles(client, kw)
            logger.info(
                "ArticleFetcher: ESPN RSS mini-articles for '%s': %d", query, len(espn_articles)
            )

            # ── Step 2: collect fetchable URLs from Google News (capped at 10) ──
            gnews_urls = await self._google_news_urls(client, query)

            logger.info(
                "ArticleFetcher: fetchable URL candidates for '%s' — Google News: %d",
                query, len(gnews_urls),
            )

            # Deduplicate URLs
            seen: set[str] = set()
            all_urls: list[str] = []
            for url in gnews_urls:
                if url not in seen:
                    seen.add(url)
                    all_urls.append(url)

            # ── Step 3: fetch full text sequentially, stop when we have enough ─
            # Start with ESPN mini-articles, then fill up with fetched articles.
            articles: list[dict] = list(espn_articles)
            for url in all_urls:
                if len(articles) >= max_articles:
                    break
                if any(domain in url for domain in _SKIP_FETCH_DOMAINS):
                    logger.debug("ArticleFetcher: skipping fetch for %s (blocked domain)", url)
                    continue
                article = await self._fetch_article_text(client, url)
                if article:
                    articles.append(article)

        logger.info(
            "ArticleFetcher: returning %d articles for '%s'",
            len(articles), query,
        )
        return articles

    # ── Google News RSS (per-query, 2h cache) ─────────────────────────────────

    async def _google_news_urls(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> list[str]:
        """
        Return article URLs from Google News RSS, using a 2h in-memory cache.

        Google News RSS uses <link/> as a self-closing empty element — the actual
        article URL is inside the <description> field as an <a href="..."> pointing
        to a news.google.com/rss/articles/CBMi... redirect URL.  We extract those
        href values; httpx follows the 302 redirect to the real article at fetch time.
        """
        cached = self._gnews_cache.get(query)
        if cached:
            fetched_at, urls = cached
            if time.time() - fetched_at < GOOGLE_NEWS_CACHE_TTL:
                logger.debug("ArticleFetcher: Google News cache hit for '%s'", query)
                return urls

        try:
            encoded = urllib.parse.quote(query)
            rss_url = (
                f"https://news.google.com/rss/search"
                f"?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            )
            resp = await client.get(rss_url)
            resp.raise_for_status()

            urls: list[str] = []
            for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                # Primary: extract href from <description><a href="..."> — these are
                # news.google.com redirect URLs that 302 to the actual article page.
                desc_m = re.search(r"<description>(.*?)</description>", item, re.DOTALL)
                if desc_m:
                    href_m = re.search(
                        r'href="(https://news\.google\.com/[^"]+)"',
                        desc_m.group(1),
                    )
                    if href_m:
                        urls.append(href_m.group(1))
                        continue

                # Fallback: some RSS flavours do put the URL in <link>
                link_m = re.search(r"<link>(https?://[^<]+)</link>", item)
                if link_m:
                    urls.append(link_m.group(1).strip())

            urls = urls[:GOOGLE_NEWS_MAX_URLS]
            self._gnews_cache[query] = (time.time(), urls)
            logger.info(
                "ArticleFetcher: Google News RSS fetched %d URLs for '%s'", len(urls), query
            )
            return urls

        except Exception as exc:
            logger.warning("ArticleFetcher: Google News RSS failed for '%s': %s", query, exc)
            return []

    # ── ESPNcricinfo RSS → mini-articles (no page fetch) ──────────────────────

    async def _espn_rss_articles(
        self,
        client: httpx.AsyncClient,
        keywords: set[str],
    ) -> list[dict]:
        """
        Return ESPNcricinfo RSS items matching any keyword as mini-articles.
        Uses the RSS title + description as the article text — no page fetch.
        This avoids the 403 on ESPN article pages while still getting editorial
        content (toss results, squad news, pitch reports often appear in summaries).
        """
        if self._espn_raw is None:
            self._espn_raw = await self._fetch_rss_items(client, ESPNCRICINFO_RSS_URL)
            logger.info(
                "ArticleFetcher: ESPNcricinfo RSS cached %d items", len(self._espn_raw)
            )

        articles: list[dict] = []
        for item in self._espn_raw:
            combined = (item["title"] + " " + item.get("description", "")).lower()
            if any(kw in combined for kw in keywords):
                text = f"{item['title']}\n\n{item.get('description', '')}".strip()
                if text:
                    articles.append({
                        "text":  text,
                        "url":   item["url"],
                        "title": item["title"],
                    })
        return articles

    async def _fetch_rss_items(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
    ) -> list[dict]:
        """Fetch an RSS feed and return [{title, url, description}] for all items."""
        try:
            resp = await client.get(feed_url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("ArticleFetcher: RSS fetch failed for %s: %s", feed_url, exc)
            return []

        items: list[dict] = []
        for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
            title_m = re.search(r"<title>(.*?)</title>",       item, re.DOTALL)
            link_m  = re.search(r"<link>(https?://[^<]+)</link>", item)
            desc_m  = re.search(r"<description>(.*?)</description>", item, re.DOTALL)

            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
            url   = link_m.group(1).strip() if link_m else ""
            desc  = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""

            if url:
                items.append({"title": title, "url": url, "description": desc})
        return items

    # ── Full article text fetcher ──────────────────────────────────────────────

    async def _fetch_article_text(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> Optional[dict]:
        """
        Fetch a single article URL and extract its main text body.
        Returns None if the page is unreachable or yields too little text.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("ArticleFetcher: beautifulsoup4 not installed")
            return None

        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code != 200:
                logger.debug("ArticleFetcher: %s → HTTP %d", url, resp.status_code)
                return None

            soup = BeautifulSoup(resp.text, "lxml")

            for tag in soup(["script", "style", "nav", "header", "footer",
                              "aside", "form", "noscript", "iframe"]):
                tag.decompose()

            body = (
                soup.select_one("article")
                or soup.select_one("[class*='article-body']")
                or soup.select_one("[class*='story-body']")
                or soup.select_one("[class*='post-content']")
                or soup.select_one("[class*='entry-content']")
                or soup.select_one("main")
            )

            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                paras = [
                    p.get_text(strip=True)
                    for p in soup.find_all("p")
                    if len(p.get_text(strip=True)) > 60
                ]
                text = "\n".join(paras)

            if len(text) < 200:
                logger.debug(
                    "ArticleFetcher: %s too short (%d chars), skipping", url, len(text)
                )
                return None

            h1    = soup.select_one("h1")
            title = h1.get_text(strip=True) if h1 else url.split("/")[-1]

            logger.info("ArticleFetcher: fetched %d chars from %s", len(text), url)
            return {
                "text":  text[:MAX_ARTICLE_CHARS],
                "url":   url,
                "title": title,
            }

        except Exception as exc:
            logger.debug("ArticleFetcher: failed to fetch %s: %s", url, exc)
            return None


# ── Module-level singleton ─────────────────────────────────────────────────────

article_fetcher = ArticleFetcher()
