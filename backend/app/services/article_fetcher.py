"""
Fetch full cricket articles from ESPNcricinfo and Cricbuzz.
Works for all cricket competitions (IPL, BBL, PSL, internationals, etc.).

Returns list of {"text": str, "url": str, "title": str} dicts.
Failures are caught and logged — callers always receive a (possibly empty) list.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Max characters per article — keeps token usage in check for the extractor
MAX_ARTICLE_CHARS = 10_000

# User-agent mimicking a real browser to avoid bot blocks on sports sites
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class ArticleFetcher:
    """Fetch and parse cricket articles from ESPNcricinfo and Cricbuzz."""

    async def fetch_match_articles(
        self,
        home_team: str,
        away_team: str,
        competition: str = "",
        max_articles: int = 3,
    ) -> list[dict]:
        """
        Fetch recent articles about an upcoming cricket match.
        Tries ESPNcricinfo first, then Cricbuzz to supplement.
        Returns list of {"text", "url", "title"}.
        """
        articles: list[dict] = []
        query = f"{home_team} vs {away_team}"
        if competition:
            query += f" {competition}"

        async with httpx.AsyncClient(
            timeout=15.0,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # ESPNcricinfo — most comprehensive cricket coverage
            try:
                espn = await self._fetch_espncricinfo(client, query, max_articles)
                articles.extend(espn)
                logger.info("ArticleFetcher: got %d articles from ESPNcricinfo for '%s'", len(espn), query)
            except Exception as exc:
                logger.warning("ArticleFetcher: ESPNcricinfo failed: %s", exc)

            # Cricbuzz supplement if still short
            if len(articles) < max_articles:
                try:
                    remaining = max_articles - len(articles)
                    cb = await self._fetch_cricbuzz(client, query, remaining)
                    articles.extend(cb)
                    logger.info("ArticleFetcher: got %d articles from Cricbuzz for '%s'", len(cb), query)
                except Exception as exc:
                    logger.warning("ArticleFetcher: Cricbuzz failed: %s", exc)

        return articles[:max_articles]

    # ── ESPNcricinfo ───────────────────────────────────────────────────────────

    async def _fetch_espncricinfo(
        self,
        client: httpx.AsyncClient,
        query: str,
        max_articles: int,
    ) -> list[dict]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("ArticleFetcher: beautifulsoup4 not installed — cannot parse HTML")
            return []

        articles: list[dict] = []
        search_url = f"https://www.espncricinfo.com/search?q={query.replace(' ', '+')}"

        try:
            resp = await client.get(search_url)
            soup = BeautifulSoup(resp.text, "lxml")

            article_links = soup.select("a[href*='/story/'], a[href*='/news/']")
            seen: set[str] = set()

            for link in article_links:
                if len(articles) >= max_articles:
                    break
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = f"https://www.espncricinfo.com{href}"
                if href in seen:
                    continue
                seen.add(href)

                try:
                    art_resp = await client.get(href)
                    art_soup = BeautifulSoup(art_resp.text, "lxml")
                    body = art_soup.select_one("article, .article-body, .story-content, [class*='story']")
                    if body:
                        text = body.get_text(separator="\n", strip=True)
                        h1 = art_soup.select_one("h1")
                        articles.append({
                            "text": text[:MAX_ARTICLE_CHARS],
                            "url": href,
                            "title": h1.get_text(strip=True) if h1 else "Untitled",
                        })
                except Exception as exc:
                    logger.debug("ArticleFetcher: failed to fetch ESPNcricinfo article %s: %s", href, exc)

        except Exception as exc:
            logger.debug("ArticleFetcher: ESPNcricinfo search failed: %s", exc)

        return articles

    # ── Cricbuzz ───────────────────────────────────────────────────────────────

    async def _fetch_cricbuzz(
        self,
        client: httpx.AsyncClient,
        query: str,
        max_articles: int,
    ) -> list[dict]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("ArticleFetcher: beautifulsoup4 not installed — cannot parse HTML")
            return []

        articles: list[dict] = []
        search_url = f"https://www.cricbuzz.com/search?q={query.replace(' ', '+')}"

        try:
            resp = await client.get(search_url)
            soup = BeautifulSoup(resp.text, "lxml")

            article_links = soup.select("a[href*='/cricket-news/']")
            seen: set[str] = set()

            for link in article_links:
                if len(articles) >= max_articles:
                    break
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = f"https://www.cricbuzz.com{href}"
                if href in seen:
                    continue
                seen.add(href)

                try:
                    art_resp = await client.get(href)
                    art_soup = BeautifulSoup(art_resp.text, "lxml")
                    body = art_soup.select_one(".cb-nws-dtl-itms, .cb-col-73, [class*='article']")
                    if body:
                        text = body.get_text(separator="\n", strip=True)
                        h1 = art_soup.select_one("h1")
                        articles.append({
                            "text": text[:MAX_ARTICLE_CHARS],
                            "url": href,
                            "title": h1.get_text(strip=True) if h1 else "Untitled",
                        })
                except Exception as exc:
                    logger.debug("ArticleFetcher: failed to fetch Cricbuzz article %s: %s", href, exc)

        except Exception as exc:
            logger.debug("ArticleFetcher: Cricbuzz search failed: %s", exc)

        return articles


# ── Module-level singleton ─────────────────────────────────────────────────────

article_fetcher = ArticleFetcher()
