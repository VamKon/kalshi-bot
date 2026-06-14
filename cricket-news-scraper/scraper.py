
#!/usr/bin/env python3
"""Cricket news scraper — fetches latest articles from Cricbuzz and publishes to Kafka."""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from kafka import KafkaProducer
from kafka.errors import KafkaError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka-kafka-bootstrap.kafka.svc.cluster.local:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "telemetry-data")
NEWS_URL = os.getenv("NEWS_URL", "https://www.cricbuzz.com/cricket-news")
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "10"))
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL_SECONDS", "3600"))
RUN_ONCE = os.getenv("RUN_ONCE", "false").lower() == "true"
FETCH_CONTENT = os.getenv("FETCH_ARTICLE_CONTENT", "true").lower() == "true"

BASE_URL = "https://www.cricbuzz.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


_ARTICLE_HREF = re.compile(r"^/cricket-news/\d+/")


def scrape_article_list(session: requests.Session) -> list[dict]:
    """Fetch Cricbuzz news listing and return up to MAX_ARTICLES stubs."""
    resp = session.get(NEWS_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    articles: list[dict] = []
    seen: set[str] = set()

    # Cricbuzz redesigned to Tailwind; match article links by numeric ID in href
    # e.g. /cricket-news/139149/title-slug  (nav links use /editorial/ instead)
    for tag in soup.find_all("a", href=_ARTICLE_HREF):
        href = tag.get("href", "")
        title = tag.get("title") or tag.get_text(strip=True)
        if not title or not href:
            continue
        full_url = href if href.startswith("http") else BASE_URL + href
        if full_url in seen:
            continue
        seen.add(full_url)
        articles.append({"title": title.strip(), "url": full_url})
        if len(articles) >= MAX_ARTICLES:
            break

    return articles[:MAX_ARTICLES]


def fetch_article_content(session: requests.Session, url: str) -> dict:
    """Fetch a single article page and extract its body text and publish time."""
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        body_el = (
            soup.select_one("div.cb-nws-dtl-itm")
            or soup.select_one("article")
            or soup.select_one("div.article-body")
            or soup.select_one("div[class*='story']")
        )
        body = body_el.get_text(separator="\n", strip=True)[:3000] if body_el else ""

        time_el = soup.select_one("time") or soup.select_one("span.cb-nws-time")
        published_at: str | None = None
        if time_el:
            published_at = time_el.get("datetime") or time_el.get_text(strip=True) or None

        return {"body": body, "published_at": published_at}
    except Exception as exc:
        logger.warning("Could not fetch content from %s: %s", url, exc)
        return {"body": "", "published_at": None}


def build_producer() -> KafkaProducer:
    logger.info("Connecting to Kafka at %s", KAFKA_BOOTSTRAP)
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        retries=5,
        acks="all",
    )


def publish_articles(producer: KafkaProducer, articles: list[dict]) -> int:
    sent = 0
    for article in articles:
        message = {
            **article,
            "source": "cricbuzz",
            "topic_type": "cricket_news",
            "id": str(uuid.uuid4()),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            future = producer.send(KAFKA_TOPIC, key=article["url"], value=message)
            future.get(timeout=10)
            logger.info("[%d] Published: %s", sent + 1, article["title"][:80])
            sent += 1
        except KafkaError as exc:
            logger.error("Failed to publish '%s': %s", article["url"], exc)
    producer.flush()
    return sent


def run_scrape(session: requests.Session, producer: KafkaProducer) -> None:
    logger.info("Scraping %s ...", NEWS_URL)
    articles = scrape_article_list(session)
    logger.info("Found %d articles on listing page", len(articles))

    if not articles:
        logger.warning("No articles found — page structure may have changed")
        return

    if FETCH_CONTENT:
        for i, article in enumerate(articles):
            logger.info("Fetching content [%d/%d]: %s", i + 1, len(articles), article["url"])
            extra = fetch_article_content(session, article["url"])
            article.update(extra)
            time.sleep(1)  # polite crawl delay

    sent = publish_articles(producer, articles)
    logger.info("Done — published %d/%d articles to topic '%s'", sent, len(articles), KAFKA_TOPIC)


def main() -> None:
    logger.info("Cricket news scraper starting up")
    logger.info("Kafka bootstrap: %s | Topic: %s", KAFKA_BOOTSTRAP, KAFKA_TOPIC)
    logger.info("News source: %s | Max articles: %d | Fetch content: %s", NEWS_URL, MAX_ARTICLES, FETCH_CONTENT)

    session = get_session()
    producer = build_producer()

    try:
        if RUN_ONCE:
            run_scrape(session, producer)
        else:
            while True:
                run_scrape(session, producer)
                logger.info("Sleeping %ds until next cycle ...", SCRAPE_INTERVAL)
                time.sleep(SCRAPE_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
    finally:
        producer.close()
        logger.info("Scraper stopped")


if __name__ == "__main__":
    main()
