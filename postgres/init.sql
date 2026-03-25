-- ============================================================
-- Kalshi Trading Bot — initial database schema
-- Run automatically by the postgres Docker image on first start.
-- Alembic handles incremental migrations in production.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio (
    id          SERIAL PRIMARY KEY,
    balance     DOUBLE PRECISION NOT NULL DEFAULT 1000.0,
    created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

-- Seed the initial paper-trading bankroll (runs only on empty table)
INSERT INTO portfolio (balance)
SELECT 1000.0
WHERE NOT EXISTS (SELECT 1 FROM portfolio);

CREATE TABLE IF NOT EXISTS trades (
    id            SERIAL PRIMARY KEY,
    market_id     VARCHAR(128)  NOT NULL,
    market_title  VARCHAR(512)  NOT NULL,
    sport         VARCHAR(32)   NOT NULL,
    side          VARCHAR(8)    NOT NULL,          -- 'yes' | 'no'
    stake         DOUBLE PRECISION NOT NULL,
    entry_price   DOUBLE PRECISION NOT NULL,
    exit_price    DOUBLE PRECISION,
    status        VARCHAR(16)   NOT NULL DEFAULT 'open',  -- open|closed|cancelled
    pnl           DOUBLE PRECISION,
    ai_reasoning     TEXT,
    confidence       DOUBLE PRECISION,
    kalshi_order_id  VARCHAR(64),
    created_at       TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    resolved_at      TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_trades_market_id ON trades (market_id);
CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_sport     ON trades (sport);

CREATE TABLE IF NOT EXISTS market_signals (
    id                 SERIAL PRIMARY KEY,
    market_id          VARCHAR(128) NOT NULL,
    sport              VARCHAR(32)  NOT NULL,
    news_sentiment     DOUBLE PRECISION,
    rule_signal        DOUBLE PRECISION,
    ai_recommendation  TEXT,
    consensus_prob     DOUBLE PRECISION,
    bookmaker_count    INTEGER,
    line_movement      VARCHAR(128),
    scanned_at         TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_market_id ON market_signals (market_id);
CREATE INDEX IF NOT EXISTS idx_signals_scanned   ON market_signals (scanned_at DESC);

-- ── Sportsbook odds cache (The Odds API) ─────────────────────────────────────
-- One row per bookmaker per event side. consensus_prob is the vig-removed
-- average across all bookmakers. Cache TTL is 6 hours (enforced in app layer).
CREATE TABLE IF NOT EXISTS sportsbook_odds (
    id             SERIAL PRIMARY KEY,
    market_id      VARCHAR(128)  NOT NULL,
    event_key      VARCHAR(256)  NOT NULL,
    sport          VARCHAR(32)   NOT NULL,
    bookmaker      VARCHAR(64)   NOT NULL,
    market_type    VARCHAR(16)   NOT NULL,    -- h2h | spread | total
    outcome        VARCHAR(128)  NOT NULL,    -- home team name
    away_team      VARCHAR(128),              -- away team name
    price          DOUBLE PRECISION,          -- American odds (nullable)
    implied_prob   DOUBLE PRECISION,
    consensus_prob DOUBLE PRECISION,          -- home consensus probability
    consensus_away DOUBLE PRECISION,          -- away consensus probability
    fetched_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_odds_market_id  ON sportsbook_odds (market_id);
CREATE INDEX IF NOT EXISTS idx_odds_event_key  ON sportsbook_odds (event_key);
CREATE INDEX IF NOT EXISTS idx_odds_sport      ON sportsbook_odds (sport);
CREATE INDEX IF NOT EXISTS idx_odds_fetched_at ON sportsbook_odds (fetched_at DESC);
