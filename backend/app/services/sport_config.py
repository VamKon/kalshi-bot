"""
Sport configuration registry.

Maps each sport the bot can trade to its known Kalshi series ticker prefixes.
Used by get_markets() to discover individual game markets via the
Series → Events → Markets hierarchy.

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
Used for soccer and any cricket leagues not in the domestic allowlist.
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
    "bbl",
    "big bash",
    "big bash league",
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
BLOCKED_COMPETITIONS: set[str] = {
    # Soccer — friendlies, qualifiers, and other low-liquidity competitions
    "concacaf",
    "conmebol",
    "international friendly",
    "nations league",
    "world cup qualifier",
    "copa america",
    "gold cup",
    "africa cup",
    "afcon",
    "afc cup",
    "caf",
    # Women's competitions (low Kalshi volume)
    "nwsl",
    # Cricket leagues NOT in ALLOWED_DOMESTIC_CRICKET_LEAGUES
    "ilt20",            # UAE International League T20
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
    "NBA": {
        "description": "NBA basketball game-winner markets",
        "known_series_prefixes": [
            "KXNBA",
            # NOTE: KXNBA is intentionally broad — it also matches draft/awards/standings
            # series (KXNBADRAFTCAT, KXNBADPOY, KXNBAWINS, etc.).  Those are handled
            # correctly: KXNBAWINS → classified as 'total' and filtered; others are
            # eliminated by the 48h time-window pre-filter (they resolve months out).
            # Per-game winner series (e.g. KXNBAGAME) are caught by this prefix too.
        ],
        "notes": "Regular season and playoffs. Series tickers follow KXNBA* pattern.",
    },
    "NFL": {
        "description": "NFL football game-winner markets",
        "known_series_prefixes": ["KXNFL", "KXSUPERBOWL"],
        "notes": "Regular season, playoffs, and Super Bowl.",
    },
    "MLS": {
        "description": "Soccer / football game-winner markets (top leagues only)",
        "known_series_prefixes": [
            "KXMLSGAME",      # MLS — primary target
            "KXEPLGAME",      # English Premier League
            "KXBUNDESGAME",   # Bundesliga
            "KXSERIEAGAME",   # Serie A
            "KXLALIGAGAME",   # La Liga
            "KXUCLGAME",      # UEFA Champions League
            "KXUELIGAME",     # UEFA Europa League
            "KXLIGUE1GAME",   # Ligue 1
            "KXLIGAMXGAME",   # Liga MX
            # Removed: KXEWSLGAME (Women's Super League — low volume)
            # Removed: KXLOSEBARCA (too narrow / single-team special)
        ],
        "notes": (
            "Top-tier club soccer only. International friendlies, qualifiers, and "
            "women's leagues are filtered via BLOCKED_COMPETITIONS."
        ),
    },
    "Cricket": {
        "description": "Cricket game markets — allowed leagues and top-12 nations only",
        "known_series_prefixes": [
            # ── International ──────────────────────────────────────────────────
            "KXT20MATCH",   # T20 internationals (confirmed active)
            "KXODI",        # ODI internationals
            "KXTEST",       # Test matches
            # ── Domestic leagues (ALLOWED_DOMESTIC_CRICKET_LEAGUES) ───────────
            "KXIPL",        # Indian Premier League (confirmed active April 2026)
            "KXPSL",        # Pakistan Super League
            "KXCPL",        # Caribbean Premier League
            "KXSA20",       # SA20 — verify prefix against live Kalshi series list
            "KXVITBLAST",   # Vitality Blast (England) — verify prefix
            "KXHUNDRED",    # The Hundred (England) — verify prefix
            # BBL note: KXBBL collides with Basketball Bundesliga on Kalshi.
            # If Kalshi uses a distinct prefix for Big Bash cricket, add it here.
            # KXCRIC removed — too generic, pulls in unrelated markets
        ],
        "notes": (
            "International matches are further filtered by ALLOWED_INTERNATIONAL_CRICKET_TEAMS "
            "(both teams must be in the top-12 nations list). "
            "Domestic leagues are filtered by ALLOWED_DOMESTIC_CRICKET_LEAGUES. "
            "SA20/Vitality Blast/Hundred prefixes need verification against the live "
            "Kalshi /series endpoint — add the correct prefix if the default is wrong."
        ),
    },
}
