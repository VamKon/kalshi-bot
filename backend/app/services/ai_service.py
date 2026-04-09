"""
AI Decision Engine — two-stage Claude pipeline to minimise API costs.

Stage 1 (claude-haiku-4-5, ~80% of calls):
  Quickly scores headlines for sentiment and flags whether the market
  is interesting enough to warrant a full Sonnet analysis.

Stage 2 (claude-sonnet-4-6, ~20% of calls):
  Full reasoning over all signals -> structured trade decision.

The static system prompt is sent with cache_control so Anthropic caches
it across calls, reducing billable input tokens significantly.
"""
import asyncio
import json
import logging
from typing import Any

import anthropic

from app.core.config import settings
from app.models.schemas import AIDecision

logger = logging.getLogger(__name__)

# ── Static system prompt (must stay under 500 tokens) ─────────────────────────
# Cached by Anthropic — not re-billed after first call.
SYSTEM_PROMPT = """\
You are a sports prediction market analyst for Kalshi paper trading.
Respond ONLY with valid JSON — no markdown, no explanation outside the JSON.

For trade decisions use exactly:
{"trade": bool, "side": "yes"|"no", "confidence": 0.0-1.0, "reasoning": "one paragraph"}

CORE CONCEPT — VALUE BETTING (read carefully):
YES price = market-implied probability YES wins. E.g. YES=0.61 means market thinks 61% chance YES wins, 39% chance NO wins.
Your job: estimate the TRUE probability for each side, then compare to the market price.

Step-by-step decision process:
1. Estimate P(YES wins) from headlines and matchup context.
2. Compute P(NO wins) = 1 - P(YES wins).
3. Edge_YES = P(YES wins) - yes_price.  Edge_NO = P(NO wins) - (1 - yes_price).
4. Pick the side with the larger positive edge (if any).
5. If max edge > 0.02: set trade=true, side=that side, confidence=your probability for that side.
6. If no side has edge > 0.02: set trade=false.

Example: YES=0.61 (market says NZ 61%, SA 39%). You think NZ 48%, SA 52%.
  Edge_YES = 0.48 - 0.61 = -0.13 (negative, don't bet YES)
  Edge_NO  = 0.52 - 0.39 = +0.13 (positive! bet NO)
  → trade=true, side="no", confidence=0.52

DO NOT set trade=false just because confidence is modest (e.g. 0.52).
A 52% estimate vs a 39% market price is a 13% edge — that IS worth trading.
Only set trade=false when edge ≤ 0.02 for both sides.
"""

# ── Stage-1 Haiku prompt ───────────────────────────────────────────────────────
HAIKU_PROMPT = """\
Headlines about: {title} ({sport})
{headlines}

Score the overall news sentiment for this team/matchup on a scale from -1.0 (very negative/underdog) to +1.0 (very positive/favourite).
Respond ONLY with JSON: {{"sentiment": <-1.0 to 1.0>}}\
"""

# ── Stage-2 Sonnet prompt (dynamic section only) ──────────────────────────────
SONNET_USER_PROMPT = """\
Market: {title} ({sport})
Type: {market_type} | Hours until game: {hours:.1f}h
YES price: {yes_price:.2f} | NO price: {no_price:.2f} | Bid-ask spread: {spread:.3f}
Sentiment: {sentiment:.3f} | Rule signal: {rule_signal:.3f}
{venue_section}{price_movement_section}{facts_section}{sport_context}{odds_section}{articles_section}Headlines:
{headlines}
"""

# Injected when full article text was freshly fetched by ArticleFetcher (cache miss).
# Capped at 2 articles × 1 500 chars to stay within the token budget (~750 tokens).
# When structured CRICKET MATCH FACTS are also present above, those take precedence
# for toss/XI/pitch data; the article excerpts add narrative context (momentum,
# expert analysis, any detail the structured extractor may have missed).
ARTICLES_SECTION_TEMPLATE = """\
ARTICLE EXCERPTS (full text — use for context not captured in structured facts above):
{articles_text}
"""

# Injected when venue is known from the Odds API home_team match.
# For NFL/NBA/MLS: "Home: {team} — playing at {stadium}"
# For cricket:     "Venue: {stadium}" (already includes city)
VENUE_SECTION_TEMPLATE = "Venue: {venue} (home team: {home_team})\n"

# Injected when we have a previous YES ask price to compare against.
# A large drop in YES price = smart money on NO; a large rise = smart money on YES.
PRICE_MOVEMENT_TEMPLATE = """\
KALSHI PRICE MOVEMENT (since last scan ~{hours_ago:.0f}h ago):
- Previous YES price: {prev_price:.3f} → Current: {curr_price:.3f} ({direction} {delta_abs:.3f})
- Interpretation: {interpretation}
"""


def _price_movement_section(prev_yes_ask: float | None, curr_yes_ask: float,
                             hours_ago: float) -> str:
    """Build the price movement block for the Sonnet prompt."""
    if prev_yes_ask is None:
        return ""
    delta = round(curr_yes_ask - prev_yes_ask, 4)
    if abs(delta) < 0.01:          # < 1 cent move — not worth mentioning
        return ""
    direction = "▲ up" if delta > 0 else "▼ down"
    if delta <= -0.05:
        interp = "significant smart money on NO — market strongly repricing away from YES"
    elif delta <= -0.02:
        interp = "moderate selling pressure on YES — worth noting"
    elif delta >= 0.05:
        interp = "significant smart money on YES — market repricing toward YES"
    elif delta >= 0.02:
        interp = "moderate buying pressure on YES — worth noting"
    else:
        interp = "small drift — may be noise"
    return PRICE_MOVEMENT_TEMPLATE.format(
        hours_ago=hours_ago,
        prev_price=prev_yes_ask,
        curr_price=curr_yes_ask,
        direction=direction,
        delta_abs=abs(delta),
        interpretation=interp,
    )

# Injected for cricket markets when NO structured facts are available (headlines only)
CRICKET_CONTEXT = """\
CRICKET SIGNALS TO PRIORITISE (if present in headlines):
- Toss result: toss winner elects to bat/bowl — in T20 this shifts win prob ~5-8%. Weight heavily.
- Playing XI / squad: key player absent (star batter or lead bowler) materially changes probability.
- Pitch/venue: batting-friendly pitches favour the chasing team; bowler-friendly pitches favour the side batting first.
- Rain: any rain forecast or wet outfield sharply reduces the favourite's edge.
- NEVER infer venue or home/away from headlines — only use verified venue if provided above.
"""

# Injected for cricket markets when structured facts ARE available (replaces CRICKET_CONTEXT)
CRICKET_FACTS_CONTEXT = """\
CRICKET ANALYSIS RULES:
1. Use ONLY the CRICKET MATCH FACTS section above — never infer venue, toss, or conditions from headlines
2. If toss data is present, weight it heavily — in T20 this shifts win prob 5-8%
3. If key players are missing from XI, adjust probability materially
4. Pitch type: spin_friendly → spin bowlers dominate; pace_friendly → swing/pace matters early; batting_friendly → high scores expected
5. Dew factor (T20 day-night): heavy dew favours the team chasing (batting second)
6. If data_quality is "insufficient", note this and reduce confidence
7. Consider if the sportsbook consensus already prices in known facts (toss, XI) or was set before them
"""

# Injected into SONNET_USER_PROMPT when sportsbook odds are available
ODDS_SECTION_TEMPLATE = """\
SPORTSBOOK CONSENSUS:
- Consensus probability: {consensus_prob:.1%}
- Bookmaker range: {min_prob:.1%} - {max_prob:.1%}
- Line movement: {movement}
- Bookmakers ({count}): {bookmaker_summary}
Your role: does the sportsbook consensus make sense given the headlines/injuries,
or is there information the line hasn't priced in yet?
"""


def _compute_rule_signal(market: dict) -> float:
    """
    Rule-based signal combining:
    - Public betting bias (fade extreme YES prices)
    - Volume/liquidity signal
    - Bid-ask spread penalty (wide spread = less informed market)

    Returns a float in roughly [-0.5, 0.5].
    """
    yes_ask = float(market.get("yes_ask_dollars") or 0.5)
    yes_bid = float(market.get("yes_bid_dollars") or 0.0)
    volume  = float(market.get("open_interest_fp") or market.get("volume") or 0)

    # Public betting bias — fade heavy public sides
    bias_signal = -0.4 if yes_ask > 0.80 else (0.2 if yes_ask < 0.40 else 0.0)

    # Volume/liquidity signal — more action = more information
    vol_signal = min(volume / 10_000, 1.0) * 0.3

    # Spread penalty — wide spread means less informed/liquid market
    spread = yes_ask - yes_bid
    spread_penalty = -min(spread / 0.10, 1.0) * 0.15   # up to -0.15 for wide spreads

    return round(bias_signal + vol_signal + spread_penalty, 3)


def _hours_until_game(market: dict) -> float:
    """Best-effort hours until game; returns 24.0 as a safe default."""
    from datetime import datetime, timezone
    close_str = market.get("expected_expiration_time") or market.get("close_time")
    if not close_str:
        return 24.0
    try:
        close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        return max(0.0, (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 24.0


class AIService:

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def _headline_text(self, headlines: list[str]) -> str:
        if not headlines:
            return "No recent headlines."
        return "\n".join(f"- {h}" for h in headlines[:8])

    @staticmethod
    def _extract_json(raw: str) -> str:
        """
        Robustly extract the first complete {...} JSON object from raw text.
        Handles:
        - Pure JSON responses
        - JSON wrapped in markdown code fences (```json ... ```)
        - JSON embedded in prose (model reasoning before/after the JSON block)
        """
        raw = raw.strip()

        # 1. Try markdown code fences first
        if "```" in raw:
            for part in raw.split("```"):
                part = part.lstrip("json").strip()
                if part.startswith("{"):
                    return part

        # 2. Find the first { and its matching } using brace-depth counting.
        # rfind("}")  is wrong when Claude appends extra text after the JSON —
        # it would include that text, producing two concatenated objects that
        # json.loads rejects with "Extra data".
        start = raw.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i, ch in enumerate(raw[start:], start):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return raw[start:i + 1]

        # 3. Return as-is and let the caller's json.loads() produce a useful error
        return raw

    async def _call_haiku(self, user: str, max_tokens: int = 96) -> str:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=max_tokens,
                messages=[
                    {"role": "user", "content": user},
                ],
            ),
        )
        return response.content[0].text if response.content else ""

    async def _call_sonnet(self, user: str, max_tokens: int = 1024) -> str:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": user},
                ],
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            ),
        )
        raw = response.content[0].text if response.content else ""
        if not raw:
            logger.warning("Sonnet returned empty content (stop_reason=%s)", response.stop_reason)
        return raw

    async def quick_filter(
        self,
        market: dict,
        sport: str,
        headlines: list[str],
        market_type: str = "other",
    ) -> tuple[bool, float, str]:
        """
        Stage 1 — Haiku (sentiment scoring only).
        Always returns interesting=True — Haiku no longer makes skip decisions.
        The rule-based pre-filters already handle all the real gating.
        Haiku's only job here is to score headline sentiment cheaply.
        Returns (interesting=True, sentiment, reason="").
        """
        headline_text = self._headline_text(headlines)
        prompt = HAIKU_PROMPT.format(
            title=market.get("title", "Unknown"),
            sport=sport,
            headlines=headline_text,
        )
        raw = ""
        try:
            raw = await self._call_haiku(prompt, max_tokens=24)
            data = json.loads(self._extract_json(raw))
            sentiment = float(data.get("sentiment", 0.0))
            logger.info(
                "Haiku [%s] %s — sentiment=%.2f",
                sport, market.get("ticker", "?"), sentiment,
            )
            return True, sentiment, ""
        except json.JSONDecodeError as exc:
            logger.warning("Haiku sentiment error: %s — raw: %r — defaulting sentiment=0.0", exc, raw[:100] if raw else "<empty>")
            return True, 0.0, ""
        except Exception as exc:
            logger.warning("Haiku sentiment error: %s — defaulting sentiment=0.0", exc)
            return True, 0.0, ""

    async def decide(
        self,
        market: dict,
        sport: str,
        sentiment: float,
        headlines: list[str],
        market_type: str = "other",
        odds_context: dict | None = None,
        prev_yes_ask: float | None = None,
        prev_scan_hours_ago: float = 2.0,
        venue: str | None = None,
        cricket_facts=None,        # Optional[CricketFacts] — avoids circular import
        articles: list[dict] | None = None,  # full article bodies from ArticleFetcher
    ) -> AIDecision:
        """
        Stage 2 — Sonnet.
        Full trade decision. Only called for markets that passed quick_filter.

        odds_context (optional) — dict from OddsService.match_market() with keys:
            consensus_prob, min_prob, max_prob, bookmaker_count, bookmakers, movement,
            home_team, away_team, venue
        venue (optional) — venue string derived from Odds API home_team via VENUE_MAP;
            passed separately so cricket toss-triggered scans (which skip Odds API)
            can still inject venue if available in the future.
        """
        yes_ask = float(market.get("yes_ask_dollars") or 0.5)
        yes_bid = float(market.get("yes_bid_dollars") or 0.0)
        spread  = round(yes_ask - yes_bid, 4)
        hours   = _hours_until_game(market)
        rule_signal = _compute_rule_signal(market)

        # ── Build optional venue section ────────────────────────────────────
        # Omitted entirely when venue is None (no Odds API match or empty home_team).
        venue_section = ""
        if venue:
            home_team = (odds_context or {}).get("home_team", "home team")
            venue_section = VENUE_SECTION_TEMPLATE.format(
                venue=venue,
                home_team=home_team,
            )

        # ── Build optional odds section ─────────────────────────────────────
        odds_section = ""
        if odds_context and odds_context.get("consensus_prob") is not None:
            bm_details = odds_context.get("bookmakers", [])
            bm_summary = ", ".join(
                f"{b['bookmaker']}:{b.get('home_prob', 0):.1%}"
                for b in bm_details[:6]   # cap at 6 to stay within token budget
            ) or "N/A"
            odds_section = ODDS_SECTION_TEMPLATE.format(
                consensus_prob=odds_context["consensus_prob"],
                min_prob=odds_context.get("min_prob", odds_context["consensus_prob"]),
                max_prob=odds_context.get("max_prob", odds_context["consensus_prob"]),
                movement=odds_context.get("movement", "No prior reading"),
                count=odds_context.get("bookmaker_count", len(bm_details)),
                bookmaker_summary=bm_summary,
            )

        headline_text = self._headline_text(headlines)
        logger.info(
            "Sonnet [%s] %s — venue=%s headlines: %s",
            sport, market.get("ticker", "?"), venue or "unknown", headline_text,
        )

        # ── Cricket facts section ───────────────────────────────────────────
        # When structured facts are available, inject them and use the stricter
        # analysis rules. When absent, fall back to headline-only cricket context.
        facts_section = ""
        if sport == "Cricket" and cricket_facts is not None:
            from app.services.cricket_extractor import format_facts_for_prompt
            home_team = (odds_context or {}).get("home_team", "home team")
            away_team = (odds_context or {}).get("away_team", "away team")
            facts_section = format_facts_for_prompt(cricket_facts, home_team, away_team)

        if sport == "Cricket" and facts_section:
            sport_context = CRICKET_FACTS_CONTEXT
        elif sport == "Cricket":
            sport_context = CRICKET_CONTEXT
        else:
            sport_context = ""

        price_mv_section = _price_movement_section(prev_yes_ask, yes_ask, prev_scan_hours_ago)
        if price_mv_section:
            logger.info(
                "Price movement [%s] %s: %.3f → %.3f (Δ%.3f)",
                sport, market.get("ticker", "?"),
                prev_yes_ask, yes_ask, yes_ask - prev_yes_ask,
            )

        # ── Build article excerpts section ──────────────────────────────────
        # Include up to 2 freshly-fetched articles (1 500 chars each ≈ 375 tokens
        # per article) so Sonnet sees the raw journalist analysis — match previews,
        # momentum signals, expert opinions — not just the structured extraction.
        # Only populated on a cache miss; on a cache hit articles=[] so this is "".
        articles_section = ""
        if articles:
            excerpts: list[str] = []
            for i, art in enumerate(articles[:2], 1):
                text = (art.get("text") or "").strip()
                if not text:
                    continue
                text = text[:1500]
                art_title = art.get("title") or f"Article {i}"
                url = art.get("url") or ""
                try:
                    domain = url.split("/")[2]
                except IndexError:
                    domain = url
                excerpts.append(f'[{i}] "{art_title}" ({domain})\n{text}')
            if excerpts:
                articles_section = ARTICLES_SECTION_TEMPLATE.format(
                    articles_text="\n\n---\n\n".join(excerpts)
                )
                logger.info(
                    "Sonnet [%s] %s — injecting %d article excerpt(s) into prompt",
                    sport, market.get("ticker", "?"), len(excerpts),
                )

        user_prompt = SONNET_USER_PROMPT.format(
            title=market.get("title", "Unknown"),
            sport=sport,
            market_type=market_type,
            hours=hours,
            yes_price=yes_ask,
            no_price=round(1 - yes_ask, 3),
            spread=spread,
            sentiment=sentiment,
            rule_signal=rule_signal,
            venue_section=venue_section,
            price_movement_section=price_mv_section,
            facts_section=facts_section,
            sport_context=sport_context,
            odds_section=odds_section,
            articles_section=articles_section,
            headlines=headline_text,
        )

        raw = ""
        try:
            raw = await self._call_sonnet(user_prompt)
            data: dict[str, Any] = json.loads(self._extract_json(raw))
            trade      = bool(data.get("trade", False))
            side       = str(data.get("side", "no"))
            confidence = float(data.get("confidence", 0.0))
            reasoning  = str(data.get("reasoning", ""))

            # Consistency check: if reasoning argues for the opposite side, abort.
            # The model sometimes writes correct chain-of-thought but puts the wrong
            # side in the JSON output — executing such a trade is worse than skipping.
            if trade and reasoning:
                reasoning_lower = reasoning.lower()
                yes_signals = ("edge_yes", "yes trade", "trade yes", "buy yes", "yes side")
                no_signals  = ("edge_no",  "no trade",  "trade no",  "buy no",  "no side", "warranting a no")
                argues_yes = any(s in reasoning_lower for s in yes_signals)
                argues_no  = any(s in reasoning_lower for s in no_signals)
                if (side == "yes" and argues_no and not argues_yes) or \
                   (side == "no"  and argues_yes and not argues_no):
                    logger.warning(
                        "Side/reasoning mismatch — JSON side=%s but reasoning argues the opposite. "
                        "Defaulting to no-trade. reasoning[:120]=%r",
                        side, reasoning[:120],
                    )
                    return AIDecision(
                        trade=False, side="no", confidence=0.0,
                        reasoning=f"[ABORTED: side/reasoning mismatch — JSON said {side} but reasoning argued the opposite] {reasoning}",
                    )

            return AIDecision(trade=trade, side=side, confidence=confidence, reasoning=reasoning)
        except json.JSONDecodeError as exc:
            logger.error("Sonnet returned non-JSON: %s — raw: %r", exc, raw[:200] if raw else "<empty>")
        except Exception as exc:
            logger.error("AI decision error: %s", exc, exc_info=True)

        return AIDecision(trade=False, side="no", confidence=0.0,
                          reasoning="AI service error — defaulting to no-trade.")

    def compute_rule_signal(self, market: dict) -> float:
        return _compute_rule_signal(market)


ai_service = AIService()
