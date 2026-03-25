"""
Market scanner — orchestrates the full scan pipeline:
  Kalshi API → sport classification → news → AI decision → paper trade
"""
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import AsyncSessionLocal
from backend.models.schemas import ScanResult
from backend.services.ai_service import ai_service
from backend.services.kalshi_client import kalshi_client
from backend.services.news_service import news_service
from backend.services.trading_service import trading_service

logger = logging.getLogger(__name__)


class MarketScanner:
    """Runs one full scan cycle across all monitored sports."""

    async def run(self) -> ScanResult:
        """Entry point called by the scheduler or the manual /scan endpoint."""
        logger.info("🔍 Starting market scan …")
        markets_scanned = 0
        trades_placed = 0

        markets = await kalshi_client.get_markets(status="open", limit=300)

        async with AsyncSessionLocal() as db:
            for market in markets:
                sport = kalshi_client.classify_sport(market)
                if sport is None:
                    continue

                markets_scanned += 1
                ticker = market.get("ticker", "UNKNOWN")

                try:
                    trade = await self._process_market(db, market, sport)
                    if trade:
                        trades_placed += 1
                except Exception as exc:
                    logger.error("Error processing market %s: %s", ticker, exc)

        result = ScanResult(
            markets_scanned=markets_scanned,
            trades_placed=trades_placed,
            timestamp=datetime.utcnow(),
        )
        logger.info(
            "✅ Scan complete — %d markets scanned, %d trades placed",
            markets_scanned,
            trades_placed,
        )
        return result

    async def _process_market(
        self, db: AsyncSession, market: dict, sport: str
    ):
        """Full pipeline for a single market."""
        ticker = market.get("ticker", "UNKNOWN")
        title = market.get("title", ticker)

        # 1. News + sentiment
        headlines = await news_service.fetch_articles(f"{title} {sport}")
        sentiment = news_service._score_text(" ".join(headlines)) if headlines else 0.0

        # 2. Rule-based signal
        rule_signal = ai_service.compute_rule_signal(market)

        # 3. AI decision
        decision = await ai_service.decide(market, sport, sentiment, headlines)

        # 4. Persist signal snapshot
        await trading_service.save_signal(
            db=db,
            market_id=ticker,
            sport=sport,
            sentiment=sentiment,
            rule_signal=rule_signal,
            ai_recommendation=decision.reasoning,
        )

        # 5. Execute paper trade (if AI says yes)
        yes_bid, yes_ask = kalshi_client.extract_best_price(market)
        entry_price = yes_ask if decision.side == "yes" else (1.0 - yes_bid)

        return await trading_service.execute_paper_trade(
            db=db,
            market=market,
            sport=sport,
            decision=decision,
            entry_price=entry_price,
        )


scanner = MarketScanner()
