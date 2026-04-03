"""
Sport configuration registry.

Maps each sport the bot can trade to its known Kalshi series ticker prefixes.
Used by get_markets() to discover individual game markets via the
Series → Events → Markets hierarchy.

Add new series prefixes here as they are discovered on the production API.

BLOCKLIST
---------
BLOCKED_COMPETITIONS filters out specific competitions/leagues by name even if
their series ticker prefix is included above.  Use this to exclude obscure
international friendlies, qualifiers, or leagues with poor sportsbook coverage
where the odds-based edge calculation is unreliable.
"""

# ── Competition blocklist ──────────────────────────────────────────────────────
# Matched case-insensitively against the `competition` field in product_metadata
# and against the market title.  Add any league/competition you don't want traded.
BLOCKED_COMPETITIONS: set[str] = {
    # International soccer friendlies & qualifiers — poor liquidity, no sharp lines
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
    # Women's competitions (low Kalshi volume, thin sportsbook coverage)
    "nwsl",
    # Add more as you encounter them — check the Markets page ticker prefix
}

# ── Cricket minnow team blocklist ──────────────────────────────────────────────
# T20 internationals between these associate/emerging nations have no sportsbook
# coverage, making our odds-based edge calculation unreliable.  Matches where
# EITHER team is in this list are skipped.
BLOCKED_CRICKET_TEAMS: set[str] = {
    "costa rica", "brazil", "argentina", "panama", "belize", "mexico",
    "canada", "usa", "united states",
    "germany", "france", "italy", "spain", "netherlands",
    "kenya", "nigeria", "ghana", "uganda", "tanzania",
    "bahrain", "kuwait", "qatar", "saudi arabia", "uae",
    "singapore", "malaysia", "thailand", "japan", "china",
    "peru", "chile", "colombia", "venezuela",
    # Add any other team you see appearing in obscure T20 markets
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
        "description": "Cricket game and futures markets",
        "known_series_prefixes": [
            "KXT20MATCH",  # T20 internationals — confirmed in series list
            "KXIPL",       # IPL per-game markets (active from April 2026)
            # KXBBL removed — Kalshi uses this prefix for Basketball Bundesliga
            "KXPSL",       # Pakistan Super League
            "KXCPL",       # Caribbean Premier League
            "KXODI",       # ODI matches
            "KXTEST",      # Test matches
            "KXCRIC",      # Generic cricket
        ],
        "notes": (
            "IPL per-game markets confirmed active April 2026 under KXIPL prefix. "
            "T20 internationals via KXT20MATCH series."
        ),
    },
}
