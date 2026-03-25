"""
AI Decision Engine — uses Claude to reason over market signals and produce
a structured trade recommendation.
"""
import json
import logging
from typing import Any

import anthropic

from backend.core.config import settings
from backend.models.schemas import AIDecision

logger = logging.getLogger(__name__)

# Prompt template ─────────────────────────────────────────────────────────────
DECISION_PROMPT = """\
You are a sports prediction market trading analyst. Analyze the following market and signals, \
then decide whether to place a trade.

## Market
- Title: {market_title}
- Sport: {sport}
- Current YES price: {yes_price:.2f} (probability)
- Current NO price: {no_price:.2f} (probability)

## Signals
- News sentiment score (−1 to +1, positive = bullish for YES): {sentiment:.3f}
- Rule-based signal (−1 to +1, combines public-bias & injury data): {rule_signal:.3f}

## News Headlines
{headlines}

## Task
Based solely on the information above, produce a JSON decision object with these exact keys:
{{
  "trade": <true|false>,
  "side": <"yes"|"no">,
  "confidence": <float 0.0–1.0>,
  "reasoning": "<one concise paragraph explaining your decision>"
}}

Rules:
- Only output valid JSON. No markdown fences.
- Set "trade": false if confidence < 0.55 or signals are ambiguous.
- "side" must still be set even when "trade" is false (your best guess).
- Confidence must be between 0.0 and 1.0.
"""


def _compute_rule_signal(market: dict) -> float:
    """
    Rule-based heuristic that combines two simple signals:
      1. Public-bias: markets with very high YES prices (>0.80) are often
         over-bet on the favourite — slight negative signal.
      2. Volume momentum: high volume suggests sharp-money participation —
         slight positive signal.
    Returns a score in [-1, 1].
    """
    yes_ask = (market.get("yes_ask") or 50) / 100
    volume = market.get("volume") or 0

    # Public-bias penalty for over-priced YES
    bias_signal = -0.4 if yes_ask > 0.80 else (0.2 if yes_ask < 0.40 else 0.0)

    # Volume momentum boost (normalised, cap at 10k contracts)
    vol_signal = min(volume / 10_000, 1.0) * 0.3

    return round(bias_signal + vol_signal, 3)


class AIService:
    """Wraps the Anthropic client and produces AIDecision objects."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def decide(
        self,
        market: dict,
        sport: str,
        sentiment: float,
        headlines: list[str],
    ) -> AIDecision:
        """
        Call Claude to analyse the market signals and return a trade decision.
        Falls back to a no-trade decision on any error.
        """
        yes_bid, yes_ask = (market.get("yes_bid") or 0) / 100, (market.get("yes_ask") or 0) / 100
        rule_signal = _compute_rule_signal(market)

        headline_text = (
            "\n".join(f"- {h}" for h in headlines[:8])
            if headlines
            else "No recent headlines available."
        )

        prompt = DECISION_PROMPT.format(
            market_title=market.get("title", "Unknown"),
            sport=sport,
            yes_price=yes_ask,
            no_price=round(1 - yes_ask, 3),
            sentiment=sentiment,
            rule_signal=rule_signal,
            headlines=headline_text,
        )

        try:
            # Use synchronous Anthropic SDK in a thread — keeps FastAPI async-friendly
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            raw = response.content[0].text.strip()
            data: dict[str, Any] = json.loads(raw)

            return AIDecision(
                trade=bool(data.get("trade", False)),
                side=str(data.get("side", "no")),
                confidence=float(data.get("confidence", 0.0)),
                reasoning=str(data.get("reasoning", "")),
            )

        except json.JSONDecodeError as exc:
            logger.error("Claude returned non-JSON: %s", exc)
        except Exception as exc:
            logger.error("AI decision error: %s", exc)

        # Safe fallback
        return AIDecision(
            trade=False,
            side="no",
            confidence=0.0,
            reasoning="AI service error — defaulting to no-trade.",
        )

    def compute_rule_signal(self, market: dict) -> float:
        return _compute_rule_signal(market)


ai_service = AIService()
