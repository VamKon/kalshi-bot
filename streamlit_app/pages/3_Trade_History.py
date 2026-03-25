"""
Page 3 — Trade History
All closed trades with win/loss indicator and AI reasoning.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch, fmt_usd
from sidebar import render_sidebar

st.set_page_config(page_title="Trade History", page_icon="📜", layout="wide")
st_autorefresh(interval=60_000, key="history_refresh")
render_sidebar()

st.title("📜 Trade History")


def kalshi_url(ticker: str) -> str:
    """Build a direct link to the Kalshi market page."""
    parts  = ticker.split("-")
    series = parts[0].lower()
    event  = "-".join(parts[:2]).lower() if len(parts) >= 2 else parts[0].lower()
    return f"https://kalshi.com/markets/{series}/{event}"


# ── Filters ─────────────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 3])
sport_filter = col1.selectbox(
    "Sport", ["All", "NFL", "NBA", "MLS", "Cricket"], index=4
)

trades = fetch("/trades", params={"status": "closed", "limit": 500})
if not trades:
    st.info("No closed trades yet.")
    st.stop()

df = pd.DataFrame(trades)

if sport_filter != "All":
    df = df[df["sport"] == sport_filter]

if df.empty:
    st.info(f"No closed trades for {sport_filter}.")
    st.stop()

# ── Summary metrics ──────────────────────────────────────────────────────────
total_pnl = df["pnl"].sum()
wins      = (df["pnl"] > 0).sum()
losses    = (df["pnl"] <= 0).sum()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Trades", len(df))
m2.metric("Wins / Losses", f"{wins} / {losses}")
m3.metric("Win Rate", f"{wins/len(df)*100:.1f}%" if len(df) else "—")
m4.metric("Total P&L", fmt_usd(total_pnl), delta=fmt_usd(total_pnl), delta_color="normal")

st.divider()

# ── Build display dataframe ──────────────────────────────────────────────────
display = df[[
    c for c in [
        "id", "sport", "market_title", "market_id", "side", "stake",
        "entry_price", "exit_price", "pnl", "confidence", "resolved_at",
    ] if c in df.columns
]].copy()

# Add Kalshi URL column
if "market_id" in display.columns:
    display["kalshi_link"] = display["market_id"].map(kalshi_url)

# Numeric formatting
if "stake" in display.columns:
    display["stake"] = display["stake"].apply(lambda x: round(float(x), 2))
if "entry_price" in display.columns:
    display["entry_price"] = display["entry_price"].apply(lambda x: round(float(x), 3) if x else None)
if "exit_price" in display.columns:
    display["exit_price"] = display["exit_price"].apply(lambda x: round(float(x), 3) if x else None)
if "pnl" in display.columns:
    display["pnl"] = display["pnl"].apply(lambda x: round(float(x), 2) if x is not None else 0.0)
if "confidence" in display.columns:
    display["confidence"] = display["confidence"].apply(
        lambda x: round(float(x) * 100, 1) if x else 0.0
    )
if "resolved_at" in display.columns:
    display["resolved_at"] = pd.to_datetime(display["resolved_at"]).dt.strftime("%b %d %H:%M")

display = display.rename(columns={
    "id":           "ID",
    "sport":        "Sport",
    "market_title": "Market",
    "market_id":    "Ticker",
    "side":         "Side",
    "stake":        "Stake ($)",
    "entry_price":  "Entry",
    "exit_price":   "Exit",
    "pnl":          "P&L ($)",
    "confidence":   "Conf (%)",
    "resolved_at":  "Resolved At",
    "kalshi_link":  "Kalshi",
})

# ── Render table ─────────────────────────────────────────────────────────────
col_config = {
    "Kalshi": st.column_config.LinkColumn(
        "Kalshi",
        display_text="View →",
        help="Open market on Kalshi",
    ),
    "P&L ($)": st.column_config.NumberColumn(
        "P&L ($)",
        format="$%.2f",
        help="Profit/loss on this trade",
    ),
    "Stake ($)": st.column_config.NumberColumn("Stake ($)", format="$%.2f"),
    "Entry":     st.column_config.NumberColumn("Entry",     format="%.3f"),
    "Exit":      st.column_config.NumberColumn("Exit",      format="%.3f"),
    "Conf (%)":  st.column_config.NumberColumn("Conf (%)",  format="%.1f%%"),
}

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config=col_config,
)

# ── Expandable AI reasoning ──────────────────────────────────────────────────
st.subheader("AI Reasoning per Trade")
for _, row in df.iterrows():
    reason   = row.get("ai_reasoning") or "No reasoning recorded."
    pnl_val  = row.get("pnl", 0) or 0
    icon     = "✅" if pnl_val >= 0 else "❌"
    market_id = row.get("market_id", "")
    kalshi_link = f" — [View on Kalshi]({kalshi_url(market_id)})" if market_id else ""
    title_short = str(row.get("market_title", ""))[:60]
    with st.expander(
        f"{icon} Trade #{int(row['id'])} — {row['sport']} — {title_short}… ({fmt_usd(pnl_val)})"
    ):
        if kalshi_link:
            st.markdown(kalshi_link)
        st.write(reason)
