"""
Page 4 — Markets
Upcoming monitored games with AI signal strength, sportsbook consensus odds,
edge vs Kalshi price, and line movement indicators.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch
from sidebar import render_sidebar

st.set_page_config(page_title="Markets", page_icon="🏟️", layout="wide")
st_autorefresh(interval=300_000, key="markets_refresh")  # 5 min — scans run every 2h
render_sidebar()

st.title("🏟️ Monitored Markets")

# ── Sport filter ─────────────────────────────────────────────────────────────
sport_filter = st.selectbox("Filter by sport", ["All", "NFL", "NBA", "MLS", "Cricket"], index=4)
params = {} if sport_filter == "All" else {"sport": sport_filter}

markets = fetch("/markets", params=params)

if not markets:
    st.warning("No markets found. Markets appear after the first scan.")
    st.stop()

import re as _re

df = pd.DataFrame(markets)

# ── Deduplicate by normalised title ─────────────────────────────────────────
# Kalshi occasionally lists the same underlying game under two different series
# tickers (e.g. a standalone market AND a parlay-wrapper market).  Keep the
# highest-volume entry per normalised title so users don't see duplicates.
if "title" in df.columns and "volume" in df.columns:
    df["_title_key"] = (
        df["title"]
        .fillna("")
        .str.lower()
        .apply(lambda s: _re.sub(r"[^a-z0-9]", "", s))
    )
    df["_vol_num"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = (
        df.sort_values("_vol_num", ascending=False)
          .drop_duplicates(subset="_title_key", keep="first")
          .drop(columns=["_title_key", "_vol_num"])
    )

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


def _hours_until(iso_str) -> str:
    """Convert an ISO timestamp to a human-readable 'Xh Ym' or 'Xm' string."""
    if not iso_str or pd.isna(iso_str):
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta_secs = int((dt - datetime.now(timezone.utc)).total_seconds())
        if delta_secs <= 0:
            return "now"
        h, rem = divmod(delta_secs, 3600)
        m = rem // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        return "—"


def _edge_badge(edge) -> str:
    """Return a colored emoji prefix based on edge direction and size."""
    if pd.isna(edge):
        return "—"
    e = float(edge)
    if e >= 0.03:
        return f"🟢 +{e:.1%}"
    elif e >= 0.02:
        return f"🟡 +{e:.1%}"
    elif e > 0:
        return f"⚪ +{e:.1%}"
    else:
        return f"🔴 {e:.1%}"


# Build display columns — include odds columns when data exists
base_cols = ["ticker", "sport", "title", "yes_bid", "yes_ask", "volume",
             "signal_strength", "game_time"]
odds_cols = ["consensus_prob", "edge_pct", "line_movement", "bookmaker_count"]

display_cols = [c for c in base_cols + odds_cols if c in df.columns]
display = df[display_cols].copy()

# Numeric coercions
for col in ("yes_bid", "yes_ask", "signal_strength", "consensus_prob", "edge_pct"):
    if col in display.columns:
        display[col] = pd.to_numeric(display[col], errors="coerce")
if "volume" in display.columns:
    display["volume"] = pd.to_numeric(display["volume"], errors="coerce")

# Add computed columns
if "game_time" in display.columns:
    display["game_in"] = display["game_time"].apply(_hours_until)
    # Sort by game_time ascending so soonest games appear at top
    display["_game_time_raw"] = pd.to_datetime(display["game_time"], utc=True, errors="coerce")
    display = display.sort_values("_game_time_raw", na_position="last")
    display = display.drop(columns=["_game_time_raw"])
if "edge_pct" in display.columns:
    display["edge_display"] = display["edge_pct"].apply(_edge_badge)

# Drop raw columns replaced by display versions
for drop_col in ("game_time", "edge_pct"):
    if drop_col in display.columns:
        display = display.drop(columns=[drop_col])

display = display.rename(columns={
    "ticker":          "Ticker",
    "sport":           "Sport",
    "title":           "Title",
    "yes_bid":         "YES Bid",
    "yes_ask":         "YES Ask",
    "volume":          "Volume",
    "signal_strength": "Signal",
    "game_in":         "Game In",
    "consensus_prob":  "Consensus",
    "edge_display":    "Edge",
    "line_movement":   "Line Movement",
    "bookmaker_count": "Books",
})

col_config: dict = {
    "YES Bid": st.column_config.NumberColumn("YES Bid", format="%.3f"),
    "YES Ask": st.column_config.NumberColumn("YES Ask", format="%.3f"),
    "Signal":  st.column_config.ProgressColumn(
        "Signal", min_value=0, max_value=1, format="%.2f",
        help="Combined AI + rule-based signal strength (0–1)",
    ),
    "Volume":  st.column_config.NumberColumn("Volume", format="%d"),
    "Game In": st.column_config.TextColumn(
        "Game In", help="Time until game resolves (from expected_expiration_time)",
    ),
}

if "Consensus" in display.columns:
    col_config["Consensus"] = st.column_config.NumberColumn(
        "Consensus", format="%.3f",
        help="Vig-removed sportsbook consensus probability (avg across 40+ books)"
    )
if "Books" in display.columns:
    col_config["Books"] = st.column_config.NumberColumn("Books", format="%d")

st.dataframe(display, use_container_width=True, hide_index=True, column_config=col_config)

# ── Actionable opportunities callout ─────────────────────────────────────────
# Show markets where sportsbook edge ≥ 2% and we don't already hold a position
if has_odds and "edge_pct" in df.columns:
    open_trades    = fetch("/trades", params={"status": "open", "limit": 200}) or []
    held_tickers   = {t.get("market_id", "") for t in open_trades}
    df_action = df[
        df["edge_pct"].notna()
        & (df["edge_pct"].astype(float) >= 0.02)
        & (~df["ticker"].isin(held_tickers))
    ].copy()

    if not df_action.empty:
        df_action["edge_pct"] = df_action["edge_pct"].astype(float)
        df_action = df_action.sort_values("edge_pct", ascending=False)
        lines = []
        for _, row in df_action.iterrows():
            edge   = float(row["edge_pct"])
            title  = str(row.get("title", row.get("ticker", "")))[:55]
            game_t = _hours_until(row.get("game_time"))
            cons   = row.get("consensus_prob")
            cons_s = f" · consensus {float(cons):.1%}" if pd.notna(cons) else ""
            lines.append(f"**{title}** — edge {edge:+.1%}{cons_s} · {game_t}")
        st.success(
            "**🎯 Potential opportunities** (edge ≥ 2%, no open position):\n\n"
            + "\n\n".join(f"• {l}" for l in lines)
        )
    else:
        if df["edge_pct"].notna().any():
            st.caption("No unhedged markets with ≥ 2% edge right now.")

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

st.divider()

# ── Scan Reasoning ────────────────────────────────────────────────────────────
st.subheader("🧠 Scan Reasoning")
st.caption("AI reasoning from the last scan for each market that reached the Sonnet stage.")

scanned = df[df["ai_recommendation"].notna()].copy() if "ai_recommendation" in df.columns else pd.DataFrame()

if scanned.empty:
    st.info("No scan reasoning available yet. Run a scan to see the AI's analysis per market.")
else:
    # Sort by absolute edge (largest first) so most interesting markets appear at top
    scanned = scanned.copy()
    scanned["_abs_edge"] = scanned.get("edge_pct", pd.Series(dtype=float)).abs().fillna(0)
    scanned = scanned.sort_values("_abs_edge", ascending=False)

    for _, row in scanned.iterrows():
        edge       = row.get("edge_pct")
        consensus  = row.get("consensus_prob")
        game_time  = row.get("game_time")
        yes_ask    = row.get("yes_ask", 0)
        sport      = row.get("sport", "")
        title      = row.get("title", "")

        edge_badge = _edge_badge(edge) if pd.notna(edge) else ""
        consensus_str = f"  ·  Consensus {float(consensus):.1%}" if pd.notna(consensus) else ""
        game_str = f"  ·  ⏱ {_hours_until(game_time)}" if game_time else ""
        label = (
            f"**{title}**  ·  {sport}  ·  YES {yes_ask:.2f}"
            f"{consensus_str}  ·  {edge_badge}{game_str}"
        )
        with st.expander(label, expanded=False):
            st.write(row["ai_recommendation"])

st.caption(f"{len(df)} markets displayed — refreshes every 60 seconds.")
