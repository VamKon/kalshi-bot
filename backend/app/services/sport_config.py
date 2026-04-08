"""
Sport configuration registry — Cricket only.

Maps Cricket to its known Kalshi series ticker prefixes.
Used by get_markets() to discover individual game markets via the
Series → Events → Markets hierarchy.

IMPORTANT: All domestic cricket prefixes end with "GAME" (e.g. KXIPLGAME)
so that tournament/futures series (e.g. KXIPL-26-MUS) are never fetched.
Only series whose ticker starts with one of these prefixes will be retrieved.

Add new series prefixes here as they are discovered on the production API.

ALLOWLISTS (cricket)
--------------------
ALLOWED_INTERNATIONAL_CRICKET_TEAMS — only international fixtures where BOTH
  teams are in this set are traded.  Any match involving a team not listed here
  (associate nations, Americas / Africa / East Asia regions etc.) is skipped.

ALLOWED_DOMESTIC_CRICKET_LEAGUES — domestic T20 leagues the bot will trade.
  All other domestic leagues (ILT20, MCA Big Bash, etc.) are blocked.  This is
  matched against `product_metadata.competition` and the market title.

BLOCKED_COMPETITIONS
--------------------
Hard blocklist matched case-insensitively against competition name / title.
Used for cricket leagues not in the domestic allowlist.
"""

# ── Allowed international cricket nations ─────────────────────────────────────
# Both teams in a match MUST appear in this set for the market to be traded.
# Matched against the lowercased market title + competition string.
ALLOWED_INTERNATIONAL_CRICKET_TEAMS: set[str] = {
    "india",
    "australia",
    "england",
    "new zealand",
    "south africa",
    "west indies",
    "sri lanka",
    "bangladesh",
    "afghanistan",
    "ireland",
    "pakistan",
    "zimbabwe",
}

# ── Allowed domestic cricket leagues ──────────────────────────────────────────
# Any domestic cricket competition NOT in this set is blocked.
# Matched case-insensitively against `product_metadata.competition` and title.
ALLOWED_DOMESTIC_CRICKET_LEAGUES: set[str] = {
    "ipl",
    "indian premier league",
    "psl",
    "pakistan super league",
    "vitality blast",
    "t20 blast",
    "sa20",
    "cpl",
    "caribbean premier league",
    "the hundred",
    "hundred",
}

# ── Competition blocklist ──────────────────────────────────────────────────────
# Matched case-insensitively against `competition` field and market title.
# Cricket leagues NOT in ALLOWED_DOMESTIC_CRICKET_LEAGUES.
BLOCKED_COMPETITIONS: set[str] = {
    "ilt20",            # UAE International League T20
    "bbl",              # Big Bash League — temporarily disabled to avoid confusion
    "big bash",
    "big bash league",
    "mca big bash",     # Malaysian/other regional Big Bash clones
    "lanka premier league",
    "lpl",
    "bangladesh premier league",
    "bpl",
    "super smash",      # New Zealand domestic T20
    "ram slam",
    "t20 challenge",    # India's domestic Vijay Hazare / Syed Mushtaq T20
    "legends league",
    "minor cricket",
}

SPORT_CONFIGS: dict[str, dict] = {
    "Cricket": {
        "description": "Cricket game-winner markets — allowed leagues and top-12 nations only",
        "known_series_prefixes": [
            # ── International (per-game series by nature — no GAME suffix needed) ──
            "KXT20MATCH",   # T20 internationals (confirmed active)
            "KXODI",        # ODI internationals
            "KXTEST",       # Test matches
            # ── Domestic leagues — GAME suffix ensures only per-game markets ──────
            # Using the full *GAME prefix prevents tournament/futures series like
            # KXIPL-26-MUS from being fetched.  Only KXIPLGAME-... tickers match.
            "KXIPLGAME",    # Indian Premier League per-game markets (April 2026 active)
            "KXPSLGAME",    # Pakistan Super League per-game markets
            "KXCPLGAME",    # Caribbean Premier League per-game markets
            "KXSA20GAME",   # SA20 per-game markets — verify prefix on live API
            "KXVITBLASTGAME",  # Vitality Blast (England) — verify prefix on live API
            "KXHUNDREDGAME",   # The Hundred (England) — verify prefix on live API
        ],
        "notes": (
            "All domestic prefixes end with 'GAME' to block tournament/futures series. "
            "International matches are further filtered by ALLOWED_INTERNATIONAL_CRICKET_TEAMS "
            "(both teams must be in the top-12 nations list). "
            "Domestic leagues are filtered by ALLOWED_DOMESTIC_CRICKET_LEAGUES. "
            "BBL (Big Bash League) is temporarily disabled — re-enable by adding 'bbl'/'big bash'/'big bash league' "
            "back to ALLOWED_DOMESTIC_CRICKET_LEAGUES, removing them from BLOCKED_COMPETITIONS, "
            "and adding 'KXBBLGAME' back to known_series_prefixes. "
            "Verify SA20/Vitality Blast/Hundred prefixes against the live "
            "Kalshi /series endpoint — add the correct prefix if the default is wrong."
        ),
    },
}
