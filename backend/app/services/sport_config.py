"""
Sport configuration registry.

Maps each sport the bot can trade to its known Kalshi series ticker prefixes.
Used by get_markets() to discover individual game markets via the
Series → Events → Markets hierarchy.

Add new series prefixes here as they are discovered on the production API.
"""

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
        "description": "Soccer / football game-winner markets (all leagues)",
        "known_series_prefixes": [
            "KXLIGAMXGAME",   # Liga MX — confirmed on production
            "KXEWSLGAME",     # England Women's Super League — confirmed
            "KXLOSEBARCA",    # Barcelona-specific — confirmed
            "KXMLSGAME",      # MLS
            "KXEPLGAME",      # English Premier League
            "KXBUNDESGAME",   # Bundesliga
            "KXSERIEAGAME",   # Serie A
            "KXLALIGAGAME",   # La Liga
            "KXUCLGAME",      # UEFA Champions League
            "KXUELIGAME",     # UEFA Europa League
            "KXLIGUE1GAME",   # Ligue 1
        ],
        "notes": "All soccer leagues grouped under MLS label for the bot.",
    },
    "Cricket": {
        "description": "Cricket game and futures markets",
        "known_series_prefixes": [
            "KXT20MATCH",  # T20 matches — confirmed in series list
            "KXIPL",       # IPL (likely Futures only as of March 2026)
            "KXBBL",       # Big Bash League
            "KXPSL",       # Pakistan Super League
            "KXCPL",       # Caribbean Premier League
            "KXODI",       # ODI matches
            "KXTEST",      # Test matches
            "KXCRIC",      # Generic cricket
        ],
        "notes": (
            "IPL currently only has Futures markets (no per-game markets) as of March 2026. "
            "T20 Match series (KXT20MATCH) has per-game markets."
        ),
    },
}
