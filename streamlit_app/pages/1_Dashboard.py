"""
Page 1 — Dashboard
Portfolio balance, cumulative P&L chart, win rate, ROI, active trade count.
Auto-refreshes every 60 s.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch, fmt_usd
from sidebar import render_sidebar

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
st_autorefresh(interval=60_000, key="dashboard_refresh")
render_sidebar()

st.title("📊 Dashboard")

# ── Backend health check ────────────────────────────────────────────────────
health = fetch("/health")
if not health:
    st.error("⚠️  **Backend unreachable** — is the FastAPI service running?")
    st.stop()

portfolio = fetch("/portfolio")
if not portfolio:
    st.warning("Portfolio data unavailable.")
    st.stop()

# ── KPI cards ───────────────────────────────────────────────────────────────
balance        = portfolio.get("balance", 0)
deployed       = portfolio.get("deployed", 0)
available_cash = portfolio.get("available_cash", balance)
pnl            = portfolio.get("total_pnl", 0)
win_rate       = portfolio.get("win_rate_pct", 0)
roi            = portfolio.get("roi_pct", 0)
active         = portfolio.get("active_trades", 0)

# Row 1: capital breakdown (4 columns)
r1c1, r1c2, r1c3, r1c4 = st.columns(4)
r1c1.metric(
    "💰 Total Balance", f"${balance:,.2f}",
    help="Current portfolio balance (cash on hand after deducting open stakes)",
)
r1c2.metric(
    "📤 Deployed", f"${deployed:,.2f}",
    help="Sum of stakes currently locked in open trades",
)
r1c3.metric(
    "💵 Available Cash", f"${available_cash:,.2f}",
    help="Capital free to place new trades (balance minus deployed)",
)
r1c4.metric(
    "🔄 Open Trades", active,
    help="Number of currently open positions",
)

st.markdown("")   # breathing room

# Row 2: performance (3 columns)
r2c1, r2c2, r2c3 = st.columns(3)
r2c1.metric(
    "📈 Total P&L", fmt_usd(pnl),
    delta=fmt_usd(pnl), delta_color="normal",
    help="Sum of all closed trade P&L",
)
r2c2.metric(
    "🎯 Win Rate", f"{win_rate:.1f}%",
    help="Percentage of closed trades that were profitable",
)
r2c3.metric(
    "📉 ROI", f"{roi:.1f}%",
    delta=f"{roi:.1f}%", delta_color="normal",
    help="Return on initial $1,000 bankroll",
)

st.divider()

# ── Charts side by side ─────────────────────────────────────────────────────
trades_raw = fetch("/trades", params={"status": "closed", "limit": 500})
all_trades = fetch("/trades", params={"limit": 500})

left, right = st.columns([2, 1])

with left:
    st.subheader("Cumulative P&L Over Time")
    if trades_raw:
        df = pd.DataFrame(trades_raw)
        if not df.empty and "resolved_at" in df.columns:
            df["resolved_at"] = pd.to_datetime(df["resolved_at"])
            df = df.sort_values("resolved_at")
            df["cumulative_pnl"] = df["pnl"].cumsum()

            fig = go.Figure()
            # Positive fill (green above zero)
            fig.add_trace(go.Scatter(
                x=df["resolved_at"], y=df["cumulative_pnl"].clip(lower=0),
                mode="none", fill="tozeroy",
                fillcolor="rgba(0,212,170,0.15)", showlegend=False, hoverinfo="skip",
            ))
            # Negative fill (red below zero)
            fig.add_trace(go.Scatter(
                x=df["resolved_at"], y=df["cumulative_pnl"].clip(upper=0),
                mode="none", fill="tozeroy",
                fillcolor="rgba(255,75,75,0.15)", showlegend=False, hoverinfo="skip",
            ))
            # Main line
            line_color = "#00d4aa" if df["cumulative_pnl"].iloc[-1] >= 0 else "#ff4b4b"
            fig.add_trace(go.Scatter(
                x=df["resolved_at"], y=df["cumulative_pnl"],
                mode="lines+markers", name="Cumulative P&L",
                line=dict(color=line_color, width=2), marker=dict(size=5),
                hovertemplate="%{x|%b %d %H:%M}<br>P&L: $%{y:.2f}<extra></extra>",
            ))
            fig.add_hline(y=0, line_dash="dash", line_color="#555", line_width=1)
            fig.update_layout(
                xaxis_title=None, yaxis_title="P&L ($)",
                hovermode="x unified", margin=dict(l=0, r=0, t=10, b=0), height=320,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(gridcolor="#2a2d3a"), yaxis=dict(gridcolor="#2a2d3a"),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("P&L chart will appear after the first closed trade.")
    else:
        st.info("No closed trades yet.")

with right:
    st.subheader("P&L by Sport")
    if all_trades:
        df_all = pd.DataFrame(all_trades)
        if not df_all.empty:
            sport_pnl = (
                df_all[df_all["status"] == "closed"]
                .groupby("sport")["pnl"].sum().reset_index()
                .sort_values("pnl", ascending=True)
            )
            if not sport_pnl.empty:
                fig2 = go.Figure(go.Bar(
                    x=sport_pnl["pnl"], y=sport_pnl["sport"],
                    orientation="h",
                    marker_color=[
                        "#00d4aa" if v >= 0 else "#ff4b4b" for v in sport_pnl["pnl"]
                    ],
                    hovertemplate="%{y}: $%{x:.2f}<extra></extra>",
                ))
                fig2.update_layout(
                    xaxis_title="P&L ($)", yaxis_title=None,
                    margin=dict(l=0, r=0, t=10, b=0), height=320,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(gridcolor="#2a2d3a"), yaxis=dict(gridcolor="#2a2d3a"),
                )
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("Sport breakdown appears after first closed trade.")
    else:
        st.info("No trade data available.")
