"""
Fetch full cricket articles for OpenRouter fact extraction.

Source priority (highest quality first):
  1. CricBuzz         — RSS content:encoded (full HTML) → page fetch fallback
  2. CricTracker      — RSS content:encoded → page fetch fallback
  3. Sportskeeda      — RSS content:encoded → page fetch fallback; good IPL/T20
  4. CricketAddictor  — RSS description only (Cloudflare blocks page fetches)
  5. ESPNcricinfo     — RSS title+description only (pages block with 403)
  6. Google News      — fallback; known paywall domains pre-filtered

Page extraction uses trafilatura (primary) with BeautifulSoup <p> fallback.
When content:encoded is ≥500 chars, the page fetch is skipped entirely.

All RSS feeds are cached with a 2-hour TTL — one network call per feed per cache
window. Stale caches are refreshed automatically so new match announcements are
picked up without a pod restart.

Google News search results are cached per-query with a 2-hour TTL.

Article fetch strategy: sequential with early exit — fetch one URL at a time,
stop as soon as max_articles successful full-text fetches are collected.
This avoids rate-limiting from simultaneous requests.

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

# If an RSS item's content:encoded text is at least this long, use it directly
# instead of fetching the article page (avoids bot-blocking round-trips).
MIN_RSS_FULL_TEXT_CHARS = 500

# How long to cache RSS feeds and Google News results (seconds)
RSS_CACHE_TTL        = 2 * 60 * 60  # 2 hours — refresh so new matches appear
GOOGLE_NEWS_CACHE_TTL = 2 * 60 * 60  # 2 hours

# Max Google News URLs to collect per query
GOOGLE_NEWS_MAX_URLS = 10

# ── RSS feed URLs ──────────────────────────────────────────────────────────────
ESPNCRICINFO_RSS_URL    = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"
CRICBUZZ_RSS_URL        = "https://www.cricbuzz.com/cricket-news/rss-feed"
CRICTRACKER_RSS_URL     = "https://www.crictracker.com/feed/"
CRICKETADDICTOR_RSS_URL = "https://www.cricketaddictor.com/feed/"
SPORTSKEEDA_RSS_URL     = "https://www.sportskeeda.com/cricket/feed"

# Domains whose article pages block bots (403 or Cloudflare challenge) —
# use RSS summary text only instead of attempting full page fetches.
_NO_FETCH_DOMAINS = {"espncricinfo.com", "cricketaddictor.com"}

# Known paywall / aggressive bot-blocking domains surfaced by Google News.
# Skipped immediately without an HTTP attempt.
_PAYWALL_DOMAINS = {
    "thecricketer.com",
    "wisden.com",
    "theathletic.com",
    "ft.com",
    "bloomberg.com",
    "wsj.com",
    "nytimes.com",
    "telegraph.co.uk",
    "thetimes.co.uk",
    "timesofindia.indiatimes.com",
    "hindustantimes.com",
    "ndtv.com",
}

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


def _domain(url: str) -> str:
    """Extract hostname from a URL, e.g. 'www.cricbuzz.com'."""
    try:
        return url.split("/")[2]
    except IndexError:
        return ""


def _is_no_fetch(url: str) -> bool:
    """True if the URL's domain is in _NO_FETCH_DOMAINS."""
    dom = _domain(url)
    return any(d in dom for d in _NO_FETCH_DOMAINS)


class ArticleFetcher:
    """Fetch full cricket article text for OpenRouter fact extraction."""

    def __init__(self) -> None:
        # Per-query Google News cache: {query: (fetched_at_epoch, [urls])}
        self._gnews_cache: dict[str, tuple[float, list[str]]] = {}

        # RSS feed caches: {attr_name: (fetched_at_epoch, [items])}
        # Keyed by the internal attribute name used in _rss_article_urls.
        self._rss_cache: dict[str, tuple[float, list[dict]]] = {}

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

        Source priority:
          1. CricBuzz    — content:encoded (full article) → page fetch fallback
          2. CricTracker — content:encoded → page fetch fallback
          3. Sportskeeda — content:encoded → page fetch fallback
          4. CricketAddictor — RSS description only (Cloudflare blocks pages)
          5. ESPN RSS mini-articles (no page fetch — pages return 403)
          6. Google News (fallback; paywall domains pre-filtered)

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
            articles: list[dict] = []

            # ── 1. CricBuzz full article pages ─────────────────────────────────
            cb_items = await self._rss_items_for_query(
                client, CRICBUZZ_RSS_URL, "_cricbuzz_raw", kw, "CricBuzz"
            )
            logger.info("ArticleFetcher: CricBuzz candidates for '%s': %d", query, len(cb_items))
            for item in cb_items:
                if len(articles) >= max_articles:
                    break
                if item.get("rss_full_text"):
                    # content:encoded had full text — no page fetch needed
                    logger.info("ArticleFetcher: using RSS full text for %s", item["url"])
                    articles.append({
                        "text":  item["description"][:MAX_ARTICLE_CHARS],
                        "url":   item["url"],
                        "title": item["title"],
                    })
                else:
                    article = await self._fetch_article_text(client, item["url"])
                    if article:
                        articles.append(article)
                    else:
                        logger.info("ArticleFetcher: no usable text from %s", item["url"])

            # ── 2. CricTracker full article pages ──────────────────────────────
            ct_items = await self._rss_items_for_query(
                client, CRICTRACKER_RSS_URL, "_crictracker_raw", kw, "CricTracker"
            )
            logger.info("ArticleFetcher: CricTracker candidates for '%s': %d", query, len(ct_items))
            for item in ct_items:
                if len(articles) >= max_articles:
                    break
                if item.get("rss_full_text"):
                    logger.info("ArticleFetcher: using RSS full text for %s", item["url"])
                    articles.append({
                        "text":  item["description"][:MAX_ARTICLE_CHARS],
                        "url":   item["url"],
                        "title": item["title"],
                    })
                else:
                    article = await self._fetch_article_text(client, item["url"])
                    if article:
                        articles.append(article)
                    else:
                        logger.info("ArticleFetcher: no usable text from %s", item["url"])

            # ── 3. Sportskeeda full article pages (good IPL/T20 coverage) ─────────
            if len(articles) < max_articles:
                sk_items = await self._rss_items_for_query(
                    client, SPORTSKEEDA_RSS_URL, "_sportskeeda_raw", kw, "Sportskeeda"
                )
                logger.info("ArticleFetcher: Sportskeeda candidates for '%s': %d", query, len(sk_items))
                for item in sk_items:
                    if len(articles) >= max_articles:
                        break
                    if item.get("rss_full_text"):
                        logger.info("ArticleFetcher: using RSS full text for %s", item["url"])
                        articles.append({
                            "text":  item["description"][:MAX_ARTICLE_CHARS],
                            "url":   item["url"],
                            "title": item["title"],
                        })
                    else:
                        article = await self._fetch_article_text(client, item["url"])
                        if article:
                            articles.append(article)
                        else:
                            logger.info("ArticleFetcher: no usable text from %s", item["url"])

            # ── 5. CricketAddictor — RSS description only (Cloudflare blocks pages)
            if len(articles) < max_articles:
                ca_items = await self._rss_items_for_query(
                    client, CRICKETADDICTOR_RSS_URL, "_cricketaddictor_raw", kw, "CricketAddictor"
                )
                logger.info(
                    "ArticleFetcher: CricketAddictor RSS descriptions for '%s': %d",
                    query, len(ca_items),
                )
                for item in ca_items:
                    if len(articles) >= max_articles:
                        break
                    text = f"{item['title']}\n\n{item.get('description', '')}".strip()
                    if text:
                        articles.append({
                            "text":  text[:MAX_ARTICLE_CHARS],
                            "url":   item["url"],
                            "title": item["title"],
                        })

            # ── 6. ESPN RSS mini-articles (no page fetch) ───────────────────────
            if len(articles) < max_articles:
                espn_articles = await self._espn_rss_articles(client, kw)
                logger.info(
                    "ArticleFetcher: ESPN RSS mini-articles for '%s': %d",
                    query, len(espn_articles),
                )
                for art in espn_articles:
                    if len(articles) >= max_articles:
                        break
                    articles.append(art)

            # ── 7. Google News fallback ─────────────────────────────────────────
            if len(articles) < max_articles:
                gnews_urls = await self._google_news_urls(client, query)
                logger.info(
                    "ArticleFetcher: Google News candidates for '%s': %d",
                    query, len(gnews_urls),
                )
                for url in gnews_urls:
                    if len(articles) >= max_articles:
                        break
                    dom = _domain(url)
                    if any(d in dom for d in _NO_FETCH_DOMAINS | _PAYWALL_DOMAINS):
                        logger.info("ArticleFetcher: skipping %s (blocked/paywall domain)", url)
                        continue
                    article = await self._fetch_article_text(client, url)
                    if article:
                        articles.append(article)
                    else:
                        logger.info("ArticleFetcher: no usable text from %s", url)

        logger.info("ArticleFetcher: returning %d articles for '%s'", len(articles), query)
        return articles

    # ── RSS feed helpers ───────────────────────────────────────────────────────

    async def _rss_items_for_query(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
        cache_key: str,
        keywords: set[str],
        label: str,
    ) -> list[dict]:
        """
        Fetch an RSS feed (with 2h TTL cache), filter items by keywords, and
        return matching items as dicts with {url, title, description}.
        """
        cached = self._rss_cache.get(cache_key)
        now = time.time()
        if cached is None or (now - cached[0]) > RSS_CACHE_TTL:
            items = await self._fetch_rss_items(client, feed_url)
            self._rss_cache[cache_key] = (now, items)
            logger.info("ArticleFetcher: %s RSS refreshed — %d items cached", label, len(items))
        else:
            items = cached[1]
            age_min = int((now - cached[0]) / 60)
            logger.debug("ArticleFetcher: %s RSS cache hit (age=%dm, %d items)", label, age_min, len(items))

        matched = [
            item for item in items
            if any(kw in (item["title"] + " " + item.get("description", "")).lower() for kw in keywords)
            and item.get("url")
        ]
        # Tag items that have enough RSS content to skip a page fetch entirely.
        for item in matched:
            item["rss_full_text"] = len(item.get("description", "")) >= MIN_RSS_FULL_TEXT_CHARS
        return matched

    async def _espn_rss_articles(
        self,
        client: httpx.AsyncClient,
        keywords: set[str],
    ) -> list[dict]:
        """
        Return ESPNcricinfo RSS items matching any keyword as mini-articles.
        Uses RSS title+description as text — no page fetch (pages return 403).
        """
        cached = self._rss_cache.get("_espn_raw")
        now = time.time()
        if cached is None or (now - cached[0]) > RSS_CACHE_TTL:
            items = await self._fetch_rss_items(client, ESPNCRICINFO_RSS_URL)
            self._rss_cache["_espn_raw"] = (now, items)
            logger.info("ArticleFetcher: ESPNcricinfo RSS refreshed — %d items cached", len(items))
        else:
            items = cached[1]

        articles: list[dict] = []
        for item in items:
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

    # ── Google News RSS (per-query, 2h cache) ─────────────────────────────────

    async def _google_news_urls(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> list[str]:
        """
        Return article URLs from Google News RSS, using a 2h in-memory cache.
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
                desc_m = re.search(r"<description>(.*?)</description>", item, re.DOTALL)
                if desc_m:
                    href_m = re.search(
                        r'href="(https://news\.google\.com/[^"]+)"',
                        desc_m.group(1),
                    )
                    if href_m:
                        urls.append(href_m.group(1))
                        continue
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

    # ── Shared RSS item parser ─────────────────────────────────────────────────

    async def _fetch_rss_items(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
    ) -> list[dict]:
        """Fetch an RSS feed and return [{title, url, description}] for all items.

        Parses both <description> and <content:encoded> — the latter is where
        most feeds (CricBuzz, Sportskeeda, etc.) put the full article HTML.
        When full content is present it is used as the description so callers
        get long-form text without needing a separate page fetch.
        """
        try:
            resp = await client.get(feed_url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("ArticleFetcher: RSS fetch failed for %s: %s", feed_url, exc)
            return []

        items: list[dict] = []
        for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
            title_m = re.search(r"<title>(.*?)</title>",          item, re.DOTALL)
            link_m  = re.search(r"<link>(https?://[^<]+)</link>", item)
            desc_m  = re.search(r"<description>(.*?)</description>", item, re.DOTALL)

            # content:encoded holds the full article HTML in many feeds.
            # It's wrapped in CDATA so we strip the markers and then all HTML tags.
            content_m = re.search(
                r"<content:encoded>(.*?)</content:encoded>", item, re.DOTALL
            )

            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
            url   = link_m.group(1).strip() if link_m else ""

            if content_m:
                raw = content_m.group(1).strip()
                # Strip CDATA wrapper if present
                raw = re.sub(r"^<!\[CDATA\[", "", raw)
                raw = re.sub(r"\]\]>$", "", raw)
                desc = re.sub(r"<[^>]+>", " ", raw)
                desc = re.sub(r"\s+", " ", desc).strip()
            elif desc_m:
                raw = desc_m.group(1).strip()
                raw = re.sub(r"^<!\[CDATA\[", "", raw)
                raw = re.sub(r"\]\]>$", "", raw)
                desc = re.sub(r"<[^>]+>", " ", raw)
                desc = re.sub(r"\s+", " ", desc).strip()
            else:
                desc = ""

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

        Uses trafilatura as the primary extractor — it handles diverse site
        layouts, strips boilerplate/nav/ads, and outperforms manual CSS selectors
        across cricket news sites. Falls back to BeautifulSoup <p> extraction
        if trafilatura returns nothing.

        Returns None if the page is unreachable or yields too little text.
        """
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code != 200:
                logger.info("ArticleFetcher: %s → HTTP %d (skipping)", url, resp.status_code)
                return None

            html = resp.text
            text: str = ""

            # ── Primary: trafilatura ───────────────────────────────────────────
            try:
                import trafilatura
                extracted = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=False,
                    no_fallback=False,
                    favor_recall=True,    # prefer more text over precision
                )
                if extracted:
                    text = extracted.strip()
            except ImportError:
                logger.debug("ArticleFetcher: trafilatura not installed, using BeautifulSoup")
            except Exception as exc:
                logger.debug("ArticleFetcher: trafilatura failed for %s: %s", url, exc)

            # ── Fallback: BeautifulSoup <p> extraction ─────────────────────────
            if len(text) < 200:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "lxml")
                    for tag in soup(["script", "style", "nav", "header", "footer",
                                     "aside", "form", "noscript", "iframe"]):
                        tag.decompose()
                    paras = [
                        p.get_text(strip=True)
                        for p in soup.find_all("p")
                        if len(p.get_text(strip=True)) > 60
                    ]
                    bs_text = "\n".join(paras)
                    if len(bs_text) > len(text):
                        text = bs_text
                except Exception as exc:
                    logger.debug("ArticleFetcher: BeautifulSoup fallback failed for %s: %s", url, exc)

            if len(text) < 200:
                logger.debug("ArticleFetcher: %s too short (%d chars), skipping", url, len(text))
                return None

            # Best-effort title from the URL slug
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").replace("_", " ").title()

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
