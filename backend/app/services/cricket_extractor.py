"""
Extract structured cricket match facts from full articles using OpenRouter.
Uses Llama 3 70B for cost-effective extraction before Claude Sonnet reasoning.

Falls back gracefully when OPENROUTER_API_KEY is not configured — callers
receive an empty CricketFacts object and the AI prompt omits the facts section.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import CricketFacts as CricketFactsDB

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# TTL for cached facts — expire 4 hours after match start so toss/XI updates
# are always fresh but we don't re-extract between scans pre-match.
FACTS_TTL_POST_MATCH_HOURS = 4


# ── Pydantic model for in-memory facts ────────────────────────────────────────

class CricketFacts(BaseModel):
    """Structured cricket match facts extracted from articles."""

    # Playing XI
    home_playing_xi: list[str] | None = None
    away_playing_xi: list[str] | None = None
    xi_status: str = "unknown"          # "confirmed" | "probable" | "unknown"

    # Toss
    toss_winner: str | None = None
    toss_decision: str | None = None    # "bat" | "field"

    # Conditions
    pitch_report: str | None = None
    weather: str | None = None
    dew_factor: str | None = None       # "expected" | "minimal" | "heavy" | "not applicable"

    # Availability
    injuries: list[dict] | None = None  # [{"player", "team", "status"}]
    late_changes: list[str] | None = None

    # Form context
    key_player_form: list[dict] | None = None   # [{"player", "note"}]
    head_to_head_venue: str | None = None
    recent_form_home: str | None = None         # "WWLWW"
    recent_form_away: str | None = None

    # Test-specific
    day_of_match: int | None = None
    session: str | None = None
    follow_on_status: str | None = None

    # Metadata
    source_urls: list[str] = []
    extraction_confidence: float = 0.0

    def is_empty(self) -> bool:
        """True when no useful facts were extracted."""
        return (
            self.toss_winner is None
            and self.home_playing_xi is None
            and self.away_playing_xi is None
            and self.pitch_report is None
            and not self.injuries
        )


# ── Extraction prompt ──────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """\
You are a cricket data extraction assistant. Extract match-relevant facts from the provided article.

This could be any cricket format: T20 (IPL, BBL, PSL, CPL, SA20, ILT20, The Hundred, International T20), ODI, or Test match.

Return ONLY valid JSON with these fields:
{
  "home_playing_xi": ["player1", "player2", ...] or null,
  "away_playing_xi": ["player1", "player2", ...] or null,
  "xi_status": "confirmed" | "probable" | "unknown",
  "toss_winner": "Team Name" or null,
  "toss_decision": "bat" | "field" or null,
  "pitch_report": "description of pitch conditions" or null,
  "weather": "clear" | "overcast" | "rain threat" | "hot and humid" | etc. or null,
  "dew_factor": "expected" | "minimal" | "heavy" | "not applicable" or null,
  "injuries": [{"player": "Name", "team": "Team", "status": "out|doubtful|questionable"}] or [],
  "late_changes": ["description of last-minute changes"] or [],
  "key_player_form": [{"player": "Name", "note": "recent form summary"}] or [],
  "head_to_head_venue": "historical note about teams at this venue" or null,
  "recent_form_home": "WWLWW" (last 5 match results) or null,
  "recent_form_away": "LWWLW" (last 5 match results) or null,
  "day_of_match": 1-5 for Test matches or null,
  "session": "morning" | "afternoon" | "evening" or null,
  "follow_on_status": "enforced" | "avoided" | "pending" or null,
  "extraction_confidence": 0.0 to 1.0
}

RULES:
- Only include facts EXPLICITLY stated in the article
- Do NOT infer or speculate
- If information is not present, use null or empty array
- Playing XI must have exactly 11 players if confirmed
- For T20/ODI, dew_factor is relevant for day-night matches
- For Tests, include day_of_match and session if mentioned
- extraction_confidence reflects how much match-relevant info was found (0 = nothing useful, 1 = toss + XI + pitch all present)
"""


# ── Main extractor class ───────────────────────────────────────────────────────

class CricketExtractor:
    """Extract structured facts from cricket articles via OpenRouter."""

    def __init__(self) -> None:
        self._enabled = bool(settings.OPENROUTER_API_KEY)
        if not self._enabled:
            logger.info("CricketExtractor: OPENROUTER_API_KEY not set — extraction disabled")

    async def extract_from_article(
        self,
        article_text: str,
        home_team: str,
        away_team: str,
        match_format: str = "T20",
        competition: str = "",
        source_url: Optional[str] = None,
    ) -> CricketFacts:
        """Extract structured facts from a single article. Returns empty facts on failure."""
        if not self._enabled:
            return CricketFacts()

        context = f"Match: {home_team} vs {away_team}"
        if competition:
            context += f" ({competition})"
        context += f"\nFormat: {match_format}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                        "HTTP-Referer": "https://kalshi.local",
                        "X-Title": "Kalshi Cricket Trading Bot",
                    },
                    json={
                        "model": settings.OPENROUTER_MODEL,
                        "messages": [
                            {"role": "system", "content": EXTRACTION_PROMPT},
                            {"role": "user", "content": f"{context}\n\nArticle:\n{article_text[:8000]}"},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                )
                response.raise_for_status()
                data = response.json()

            content = data["choices"][0]["message"]["content"]

            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            parsed = json.loads(content.strip())
            facts = CricketFacts(**{k: v for k, v in parsed.items() if k in CricketFacts.model_fields})
            if source_url:
                facts.source_urls = [source_url]

            logger.info(
                "CricketExtractor: extracted facts (confidence=%.2f, toss=%s, xi=%s) from %s",
                facts.extraction_confidence,
                facts.toss_winner,
                facts.xi_status,
                source_url or "unknown",
            )
            return facts

        except Exception as exc:
            logger.warning("CricketExtractor: extraction failed for %s: %s", source_url, exc)
            return CricketFacts()

    async def extract_from_multiple(
        self,
        articles: list[dict],
        home_team: str,
        away_team: str,
        match_format: str = "T20",
        competition: str = "",
    ) -> CricketFacts:
        """Extract and merge facts from multiple articles."""
        merged = CricketFacts()
        all_urls: list[str] = []

        for article in articles:
            facts = await self.extract_from_article(
                article.get("text", ""),
                home_team,
                away_team,
                match_format,
                competition,
                article.get("url"),
            )
            merged = _merge_facts(merged, facts)
            all_urls.extend(facts.source_urls)

        merged.source_urls = all_urls
        return merged


# ── Facts cache (PostgreSQL-backed) ───────────────────────────────────────────

class CricketFactsCache:
    """
    Persist and retrieve CricketFacts from the cricket_facts DB table.
    TTL: match commence_time + FACTS_TTL_POST_MATCH_HOURS hours.
    """

    async def get(self, db: AsyncSession, event_key: str) -> Optional[CricketFacts]:
        """Return cached facts if they exist and haven't expired."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            result = await db.execute(
                select(CricketFactsDB)
                .where(CricketFactsDB.event_key == event_key)
                .where(
                    (CricketFactsDB.expires_at == None) |  # noqa: E711
                    (CricketFactsDB.expires_at > now)
                )
                .order_by(desc(CricketFactsDB.extracted_at))
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None

            return CricketFacts(
                home_playing_xi=row.home_playing_xi,
                away_playing_xi=row.away_playing_xi,
                xi_status=row.xi_status or "unknown",
                toss_winner=row.toss_winner,
                toss_decision=row.toss_decision,
                pitch_report=row.pitch_report,
                weather=row.weather,
                dew_factor=row.dew_factor,
                injuries=row.injuries,
                late_changes=row.late_changes,
                key_player_form=row.key_player_form,
                head_to_head_venue=row.head_to_head_venue,
                recent_form_home=row.recent_form_home,
                recent_form_away=row.recent_form_away,
                day_of_match=row.day_of_match,
                session=row.session,
                follow_on_status=row.follow_on_status,
                source_urls=row.source_urls or [],
                extraction_confidence=row.extraction_confidence or 0.0,
            )
        except Exception as exc:
            logger.warning("CricketFactsCache.get failed: %s", exc)
            return None

    async def set(
        self,
        db: AsyncSession,
        event_key: str,
        facts: CricketFacts,
        commence_time: Optional[datetime] = None,
    ) -> None:
        """Persist facts to DB. expire_at = commence_time + 4h (or None if unknown)."""
        try:
            expires_at = None
            if commence_time:
                if commence_time.tzinfo is not None:
                    commence_time = commence_time.replace(tzinfo=None)
                expires_at = commence_time + timedelta(hours=FACTS_TTL_POST_MATCH_HOURS)

            row = CricketFactsDB(
                event_key=event_key,
                home_playing_xi=facts.home_playing_xi,
                away_playing_xi=facts.away_playing_xi,
                xi_status=facts.xi_status,
                toss_winner=facts.toss_winner,
                toss_decision=facts.toss_decision,
                pitch_report=facts.pitch_report,
                weather=facts.weather,
                dew_factor=facts.dew_factor,
                injuries=facts.injuries,
                late_changes=facts.late_changes,
                key_player_form=facts.key_player_form,
                head_to_head_venue=facts.head_to_head_venue,
                recent_form_home=facts.recent_form_home,
                recent_form_away=facts.recent_form_away,
                day_of_match=facts.day_of_match,
                session=facts.session,
                follow_on_status=facts.follow_on_status,
                source_urls=facts.source_urls,
                extraction_confidence=facts.extraction_confidence,
                expires_at=expires_at,
            )
            db.add(row)
            await db.commit()
            logger.info("CricketFactsCache: saved facts for event_key=%s", event_key)
        except Exception as exc:
            logger.warning("CricketFactsCache.set failed: %s", exc)
            await db.rollback()


# ── Module-level singletons ────────────────────────────────────────────────────

cricket_extractor = CricketExtractor()
cricket_facts_cache = CricketFactsCache()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _merge_facts(base: CricketFacts, new: CricketFacts) -> CricketFacts:
    """Merge two CricketFacts, preferring non-null/non-empty values from `new`."""
    merged = base.model_dump()
    for key, value in new.model_dump().items():
        if value is not None and value != [] and value != "unknown" and value != 0.0:
            merged[key] = value
    merged["extraction_confidence"] = max(
        base.extraction_confidence,
        new.extraction_confidence,
    )
    return CricketFacts(**merged)


def format_facts_for_prompt(
    facts: CricketFacts,
    home_team: str,
    away_team: str,
) -> str:
    """
    Format CricketFacts as a compact prompt section for Claude Sonnet.
    Returns empty string when facts are empty (so the prompt section is omitted).
    """
    if facts.is_empty():
        return ""

    lines = ["CRICKET MATCH FACTS (extracted from articles):"]

    if facts.toss_winner and facts.toss_decision:
        lines.append(f"- Toss: {facts.toss_winner} won and elected to {facts.toss_decision}")
    elif facts.toss_winner:
        lines.append(f"- Toss: {facts.toss_winner} won toss (decision unknown)")

    if facts.pitch_report:
        lines.append(f"- Pitch: {facts.pitch_report}")

    if facts.weather:
        lines.append(f"- Weather: {facts.weather}")

    if facts.dew_factor and facts.dew_factor != "not applicable":
        lines.append(f"- Dew factor: {facts.dew_factor}")

    if facts.home_playing_xi:
        xi_str = ", ".join(facts.home_playing_xi[:11])
        lines.append(f"- {home_team} XI ({facts.xi_status}): {xi_str}")

    if facts.away_playing_xi:
        xi_str = ", ".join(facts.away_playing_xi[:11])
        lines.append(f"- {away_team} XI ({facts.xi_status}): {xi_str}")

    if facts.injuries:
        for inj in (facts.injuries or [])[:4]:
            lines.append(f"- Injury: {inj.get('player')} ({inj.get('team')}) — {inj.get('status')}")

    if facts.late_changes:
        for change in (facts.late_changes or [])[:3]:
            lines.append(f"- Late change: {change}")

    if facts.key_player_form:
        for pf in (facts.key_player_form or [])[:4]:
            lines.append(f"- Form: {pf.get('player')} — {pf.get('note')}")

    if facts.recent_form_home:
        lines.append(f"- {home_team} recent form (last 5): {facts.recent_form_home}")

    if facts.recent_form_away:
        lines.append(f"- {away_team} recent form (last 5): {facts.recent_form_away}")

    if facts.head_to_head_venue:
        lines.append(f"- H2H at venue: {facts.head_to_head_venue}")

    # Test-specific
    if facts.day_of_match:
        lines.append(f"- Test match day: {facts.day_of_match}")
    if facts.session:
        lines.append(f"- Session: {facts.session}")
    if facts.follow_on_status:
        lines.append(f"- Follow-on: {facts.follow_on_status}")

    lines.append(f"- Data quality: {facts.xi_status} (confidence: {facts.extraction_confidence:.0%})")
    return "\n".join(lines) + "\n"
