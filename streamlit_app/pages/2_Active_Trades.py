"""
Page 2 — Active Trades
Table of open positions with unrealised P&L estimate.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch, fmt_usd
from sidebar import render_sidebar


def _hours_until(iso_str) -> str:
    if not iso_str or pd.isna(iso_str):
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = int((dt - datetime.now(timezone.utc)).total_seconds())
        if delta <= 0:
            return "⏰ now"
        h, rem = divmod(delta, 3600)
        m = rem // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        return "—"

st.set_page_config(page_title="Active Trades", page_icon="📋", layout="wide")
st_autorefresh(interval=60_000, key="active_refresh")
render_sidebar()

st.title("📋 Active Trades")

trades = fetch("/trades", params={"status": "open", "limit": 200})

if not trades:
    st.info("No open trades right now. The bot will place trades on the next scan.")
    st.stop()

df = pd.DataFrame(trades)

# Build a game_time lookup from the markets endpoint (best-effort, no error if unavailable)
markets_data = fetch("/markets") or []
game_time_by_ticker: dict[str, str] = {
    m["ticker"]: m.get("game_time") or ""
    for m in markets_data
    if m.get("ticker")
}


def kalshi_url(ticker: str) -> str:
    """Build a direct link to the Kalshi market page."""
    parts  = ticker.split("-")
    series = parts[0].lower()
    event  = "-".join(parts[:2]).lower() if len(parts) >= 2 else parts[0].lower()
    return f"https://kalshi.com/markets/{series}/{event}"


# ── Build display dataframe ─────────────────────────────────────────────────
display_cols = [
    "id", "sport", "market_title", "market_id", "side",
    "stake", "entry_price", "confidence", "created_at",
]
df_display = df[[c for c in display_cols if c in df.columns]].copy()

# Add Kalshi URL column (raw URL — rendered via LinkColumn)
if "market_id" in df_display.columns:
    df_display["kalshi_link"] = df_display["market_id"].map(kalshi_url)

# Format columns for readability
if "stake" in df_display.columns:
    df_display["stake"] = df_display["stake"].apply(lambda x: round(float(x), 2))
if "entry_price" in df_display.columns:
    df_display["entry_price"] = df_display["entry_price"].apply(lambda x: round(float(x), 3))
if "confidence" in df_display.columns:
    df_display["confidence"] = df_display["confidence"].apply(
        lambda x: round(float(x) * 100, 1) if x else 0.0
    )
if "created_at" in df_display.columns:
    df_display["created_at"] = pd.to_datetime(df_display["created_at"]).dt.strftime("%b %d %H:%M")
# Resolves in — look up game_time from markets, fall back to "—"
if "market_id" in df_display.columns:
    df_display["resolves_in"] = df_display["market_id"].map(
        lambda tid: _hours_until(game_time_by_ticker.get(tid, ""))
    )
# Color-coded YES/NO badge
if "side" in df_display.columns:
    df_display["side"] = df_display["side"].apply(
        lambda s: "🟢 YES" if str(s).lower() == "yes" else "🔴 NO"
    )

df_display = df_display.rename(columns={
    "id":           "ID",
    "sport":        "Sport",
    "market_title": "Market",
    "market_id":    "Ticker",
    "side":         "Side",
    "stake":        "Stake ($)",
    "entry_price":  "Entry Price",
    "confidence":   "AI Conf (%)",
    "created_at":   "Opened At",
    "resolves_in":  "Resolves In",
    "kalshi_link":  "Kalshi",
})

# ── Render table ────────────────────────────────────────────────────────────
col_config = {
    "Kalshi": st.column_config.LinkColumn(
        "Kalshi",
        display_text="View →",
        help="Open market on Kalshi",
    ),
    "AI Conf (%)": st.column_config.NumberColumn(
        "AI Conf (%)",
        format="%.1f%%",
        help="Claude's confidence score for this trade",
    ),
    "Stake ($)": st.column_config.NumberColumn(
        "Stake ($)",
        format="$%.2f",
    ),
    "Entry Price": st.column_config.NumberColumn(
        "Entry Price",
        format="%.3f",
    ),
    "Resolves In": st.column_config.TextColumn(
        "Resolves In",
        help="Estimated time until game ends and Kalshi settles the market",
    ),
}

st.dataframe(
    df_display,
    use_container_width=True,
    hide_index=True,
    column_config=col_config,
)

st.caption(f"**{len(df_display)} open trade(s)** — refreshes every 60 seconds.")

# ── AI Reasoning per Active Trade ───────────────────────────────────────────
st.subheader("AI Reasoning per Trade")
for _, row in df.iterrows():
    reason    = row.get("ai_reasoning") or "No reasoning recorded."
    conf      = row.get("confidence")
    conf_str  = f" — {round(float(conf) * 100, 1)}% conf" if conf else ""
    market_id = row.get("market_id", "")
    kalshi_link = f" — [View on Kalshi]({kalshi_url(market_id)})" if market_id else ""
    title_short = str(row.get("market_title", ""))[:60]
    sport     = row.get("sport", "")
    side      = str(row.get("side", "")).upper()
    stake     = row.get("stake")
    stake_str = f" — {fmt_usd(float(stake))}" if stake else ""
    with st.expander(
        f"🟢 Trade #{int(row['id'])} — {sport} — {title_short}… ({side}{stake_str}{conf_str})"
    ):
        if kalshi_link:
            st.markdown(kalshi_link)
        st.write(reason)

# ── Manual trade resolution (useful for testing) ────────────────────────────
with st.expander("🔧 Manually Resolve a Trade (testing only)"):
    col1, col2, col3, col4 = st.columns(4)
    trade_id   = col1.number_input("Trade ID", min_value=1, step=1, value=1)
    outcome    = col2.selectbox("Outcome", ["win", "loss"])
    exit_price = col3.number_input("Exit Price (0–1)", min_value=0.0, max_value=1.0, value=0.5, step=0.01)
    if col4.button("Resolve", use_container_width=True):
        import httpx
        backend = os.getenv("BACKEND_URL", "http://localhost:8000")
        resp = httpx.post(
            f"{backend}/api/v1/trades/{trade_id}/resolve",
            params={"outcome": outcome, "exit_price": exit_price},
            timeout=10.0,
        )
        if resp.status_code == 200:
            st.success(f"Trade {trade_id} resolved as **{outcome}**.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(f"Error: {resp.text}")
