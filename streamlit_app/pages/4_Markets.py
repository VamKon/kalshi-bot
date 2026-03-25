"""
Page 4 — Markets
Upcoming monitored games with AI signal strength, sportsbook consensus odds,
edge vs Kalshi price, and line movement indicators.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch
from sidebar import render_sidebar

st.set_page_config(page_title="Markets", page_icon="🏟️", layout="wide")
st_autorefresh(interval=60_000, key="markets_refresh")
render_sidebar()

st.title("🏟️ Monitored Markets")

# ── Sport filter ─────────────────────────────────────────────────────────────
sport_filter = st.selectbox("Filter by sport", ["All", "NFL", "NBA", "MLS", "Cricket"], index=4)
params = {} if sport_filter == "All" else {"sport": sport_filter}

markets = fetch("/markets", params=params)

if not markets:
    st.warning("No markets found. Markets appear after the first scan.")
    st.stop()

df = pd.DataFrame(markets)

# ── Sportsbook Consensus vs Kalshi Price ────────────────────────────────────
has_odds = "consensus_prob" in df.columns and df["consensus_prob"].notna().any()

if has_odds:
    st.subheader("📊 Sportsbook Consensus vs Kalshi Price")
    df_odds = df[df["consensus_prob"].notna()].copy()

    for col in ("consensus_prob", "yes_ask", "edge_pct"):
        if col in df_odds.columns:
            df_odds[col] = pd.to_numeric(df_odds[col], errors="coerce")

    if not df_odds.empty:
        # Scatter: Kalshi YES Ask (x) vs Sportsbook Consensus (y)
        # Points above the diagonal → sportsbook thinks Kalshi underpriced
        df_odds["label"] = df_odds["title"].str[:40] + "…"
        df_odds["edge_color"] = df_odds["edge_pct"].apply(
            lambda e: "positive" if (e or 0) > 0 else "negative"
        )

        fig = px.scatter(
            df_odds,
            x="yes_ask",
            y="consensus_prob",
            color="edge_pct",
            color_continuous_scale=["#ff4b4b", "#ffdd57", "#00d4aa"],
            color_continuous_midpoint=0,
            hover_name="label",
            hover_data={"yes_ask": ":.3f", "consensus_prob": ":.3f", "edge_pct": ":.3f"},
            labels={
                "yes_ask":        "Kalshi YES Ask (market price)",
                "consensus_prob": "Sportsbook Consensus Prob",
                "edge_pct":       "Edge (consensus − kalshi)",
            },
            title="Sportsbook Consensus vs Kalshi Implied Probability",
        )
        # Diagonal reference line (y = x → no edge)
        fig.add_shape(
            type="line", x0=0, y0=0, x1=1, y1=1,
            line=dict(color="#555", dash="dash", width=1),
        )
        fig.update_layout(
            height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="#2a2d3a", range=[0, 1]),
            yaxis=dict(gridcolor="#2a2d3a", range=[0, 1]),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Points **above the diagonal** → sportsbooks assign a higher win probability "
            "than Kalshi — potential long YES edge. Points **below** → potential long NO edge."
        )

    st.divider()

# ── AI Signal Strength bar chart ─────────────────────────────────────────────
st.subheader("AI Signal Strength by Market")
df_sig = df[df["signal_strength"].notna()].copy()

if not df_sig.empty:
    df_sig = df_sig.sort_values("signal_strength", ascending=False).head(20)
    df_sig["label"] = df_sig["title"].str[:50] + "…"
    fig = px.bar(
        df_sig,
        x="signal_strength",
        y="label",
        orientation="h",
        color="signal_strength",
        color_continuous_scale=["#ff4b4b", "#ffdd57", "#00d4aa"],
        range_color=[0, 1],
        labels={"signal_strength": "Signal Strength (0–1)", "label": ""},
        title="Top 20 Markets by AI Signal",
    )
    fig.update_layout(
        yaxis={"autorange": "reversed"},
        height=500,
        margin=dict(l=0, r=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor="#2a2d3a"),
        yaxis_title=None,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Signal data will appear after the first scan runs.")

st.divider()

# ── Market table ─────────────────────────────────────────────────────────────
st.subheader("All Monitored Markets")

# Build display columns — include odds columns when data exists
base_cols = ["ticker", "sport", "title", "yes_bid", "yes_ask", "volume",
             "signal_strength", "close_time"]
odds_cols = ["consensus_prob", "edge_pct", "line_movement", "bookmaker_count"]

display_cols = [c for c in base_cols + odds_cols if c in df.columns]
display = df[display_cols].copy()

# Numeric coercions
for col in ("yes_bid", "yes_ask", "signal_strength", "consensus_prob", "edge_pct"):
    if col in display.columns:
        display[col] = pd.to_numeric(display[col], errors="coerce")
if "volume" in display.columns:
    display["volume"] = pd.to_numeric(display["volume"], errors="coerce")

display = display.rename(columns={
    "ticker":          "Ticker",
    "sport":           "Sport",
    "title":           "Title",
    "yes_bid":         "YES Bid",
    "yes_ask":         "YES Ask",
    "volume":          "Volume",
    "signal_strength": "Signal",
    "close_time":      "Closes At",
    "consensus_prob":  "Consensus Prob",
    "edge_pct":        "Edge %",
    "line_movement":   "Line Movement",
    "bookmaker_count": "Books",
})

col_config: dict = {
    "YES Bid":  st.column_config.NumberColumn("YES Bid",  format="%.3f"),
    "YES Ask":  st.column_config.NumberColumn("YES Ask",  format="%.3f"),
    "Signal":   st.column_config.ProgressColumn(
        "Signal", min_value=0, max_value=1, format="%.2f",
        help="Combined AI + rule-based signal strength (0–1)",
    ),
    "Volume":   st.column_config.NumberColumn("Volume", format="%d"),
}

if "Consensus Prob" in display.columns:
    col_config["Consensus Prob"] = st.column_config.NumberColumn(
        "Consensus Prob", format="%.3f",
        help="Vig-removed sportsbook consensus probability (avg across 40+ books)"
    )
if "Edge %" in display.columns:
    col_config["Edge %"] = st.column_config.NumberColumn(
        "Edge %", format="%.3f",
        help="Edge = Consensus Prob − Kalshi YES Ask. Positive = books think YES underpriced."
    )
if "Books" in display.columns:
    col_config["Books"] = st.column_config.NumberColumn("Books", format="%d")

st.dataframe(display, use_container_width=True, hide_index=True, column_config=col_config)

if not has_odds:
    _settings = fetch("/settings") or {}
    if _settings.get("odds_api_key_configured"):
        st.info(
            "📡 Odds API key is active but no sportsbook data matched today's markets. "
            "This is expected when active markets are international fixtures (e.g. NZ vs SA T20) "
            "not covered by the IPL odds feed. Check backend logs for `OddsService match` after the next scan."
        )
    else:
        st.info(
            "💡 Set `ODDS_API_KEY` to unlock sportsbook consensus odds, edge %, "
            "and line movement indicators. Free tier: 500 requests/month at the-odds-api.com"
        )

st.caption(f"{len(df)} markets displayed — refreshes every 60 seconds.")
