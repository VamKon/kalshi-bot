"""
Page 6 — Analytics
Deep-dive into bot performance: confidence calibration, sport breakdown,
stake distribution, win-streak analysis.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import fetch, fmt_usd
from sidebar import render_sidebar

st.set_page_config(page_title="Analytics", page_icon="🔬", layout="wide")
st_autorefresh(interval=60_000, key="analytics_refresh")
render_sidebar()

st.title("🔬 Analytics")

# ── Fetch data ───────────────────────────────────────────────────────────────
trades_raw = fetch("/trades", params={"status": "closed", "limit": 500})

if not trades_raw:
    st.info("Analytics will appear after the first closed trade.")
    st.stop()

df = pd.DataFrame(trades_raw)
if df.empty:
    st.info("No closed trades yet.")
    st.stop()

df["pnl"]        = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
df["confidence"] = pd.to_numeric(df.get("confidence"), errors="coerce")
df["stake"]      = pd.to_numeric(df.get("stake"), errors="coerce")
df["win"]        = df["pnl"] > 0

# ── Top-level summary ────────────────────────────────────────────────────────
total_trades = len(df)
wins         = df["win"].sum()
total_pnl    = df["pnl"].sum()
avg_stake    = df["stake"].mean() if "stake" in df.columns else 0
avg_conf     = df["confidence"].mean() * 100 if "confidence" in df.columns else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Closed Trades", total_trades)
c2.metric("Wins / Losses", f"{wins} / {total_trades - wins}")
c3.metric("Win Rate", f"{wins/total_trades*100:.1f}%")
c4.metric("Total P&L", fmt_usd(total_pnl))
c5.metric("Avg AI Confidence", f"{avg_conf:.1f}%")

st.divider()

row1_left, row1_right = st.columns(2)

# ── Confidence calibration chart ─────────────────────────────────────────────
with row1_left:
    st.subheader("Confidence Calibration")
    st.caption("How often the bot wins at each confidence level.")
    df_conf = df.dropna(subset=["confidence"]).copy()
    if not df_conf.empty:
        bins       = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
        bin_labels = ["50–55%","55–60%","60–65%","65–70%","70–75%","75–80%","80–85%","85–90%","90%+"]
        df_conf["conf_bin"] = pd.cut(df_conf["confidence"], bins=bins, labels=bin_labels, right=False)
        cal = (
            df_conf.groupby("conf_bin", observed=True)
            .agg(win_rate=("win", "mean"), count=("win", "size"))
            .reset_index()
        )
        cal["win_rate_pct"] = cal["win_rate"] * 100

        fig = go.Figure()
        # Perfect calibration diagonal
        fig.add_trace(go.Scatter(
            x=bin_labels,
            y=[52.5, 57.5, 62.5, 67.5, 72.5, 77.5, 82.5, 87.5, 95.0],
            mode="lines",
            line=dict(dash="dash", color="#555", width=1),
            name="Perfect calibration",
        ))
        fig.add_trace(go.Bar(
            x=cal["conf_bin"].astype(str),
            y=cal["win_rate_pct"],
            text=cal["count"].map(lambda n: f"n={n}"),
            textposition="outside",
            marker_color=[
                "#00d4aa" if v >= 50 else "#ff4b4b" for v in cal["win_rate_pct"]
            ],
            name="Actual win rate",
        ))
        fig.update_layout(
            xaxis_title="AI Confidence Bucket",
            yaxis_title="Actual Win Rate (%)",
            yaxis_range=[0, 110],
            margin=dict(l=0, r=0, t=10, b=0),
            height=320,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="#2a2d3a"),
            yaxis=dict(gridcolor="#2a2d3a"),
            legend=dict(orientation="h", y=-0.25),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Confidence calibration requires trades with confidence scores.")

# ── Win rate & P&L by sport ───────────────────────────────────────────────────
with row1_right:
    st.subheader("Win Rate by Sport")
    sport_stats = (
        df.groupby("sport")
        .agg(
            trades=("win", "size"),
            wins=("win", "sum"),
            pnl=("pnl", "sum"),
        )
        .reset_index()
    )
    sport_stats["win_rate"] = sport_stats["wins"] / sport_stats["trades"] * 100

    fig3 = go.Figure()
    fig3.add_trace(go.Bar(
        x=sport_stats["sport"],
        y=sport_stats["win_rate"],
        name="Win Rate (%)",
        marker_color="#00d4aa",
        text=sport_stats["trades"].map(lambda n: f"n={n}"),
        textposition="outside",
        yaxis="y",
    ))
    fig3.add_trace(go.Scatter(
        x=sport_stats["sport"],
        y=sport_stats["pnl"],
        mode="markers+lines",
        name="P&L ($)",
        marker=dict(size=10, color="#ffdd57"),
        line=dict(color="#ffdd57", width=2, dash="dot"),
        yaxis="y2",
    ))
    fig3.update_layout(
        yaxis=dict(title="Win Rate (%)", gridcolor="#2a2d3a"),
        yaxis2=dict(title="P&L ($)", overlaying="y", side="right"),
        margin=dict(l=0, r=40, t=10, b=0),
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.25),
    )
    st.plotly_chart(fig3, use_container_width=True)

st.divider()

row2_left, row2_right = st.columns(2)

# ── P&L distribution histogram ───────────────────────────────────────────────
with row2_left:
    st.subheader("P&L Distribution")
    fig4 = go.Figure()
    fig4.add_trace(go.Histogram(
        x=df["pnl"],
        nbinsx=20,
        marker_color="#00d4aa",
        opacity=0.8,
    ))
    fig4.add_vline(x=0, line_dash="dash", line_color="#ff4b4b", line_width=2)
    fig4.update_layout(
        xaxis_title="P&L per Trade ($)",
        yaxis_title="Count",
        margin=dict(l=0, r=0, t=10, b=0),
        height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor="#2a2d3a"),
        yaxis=dict(gridcolor="#2a2d3a"),
    )
    st.plotly_chart(fig4, use_container_width=True)

# ── Stake distribution & efficiency ──────────────────────────────────────────
with row2_right:
    st.subheader("Avg P&L per Stake Dollar")
    if "stake" in df.columns and df["stake"].notna().any():
        df_eff = df.dropna(subset=["stake"]).copy()
        df_eff["efficiency"] = df_eff["pnl"] / df_eff["stake"].clip(lower=0.01)
        eff_sport = (
            df_eff.groupby("sport")["efficiency"]
            .mean().reset_index()
            .sort_values("efficiency", ascending=True)
        )
        fig5 = go.Figure(go.Bar(
            x=eff_sport["efficiency"],
            y=eff_sport["sport"],
            orientation="h",
            marker_color=[
                "#00d4aa" if v >= 0 else "#ff4b4b" for v in eff_sport["efficiency"]
            ],
            hovertemplate="%{y}: %{x:.3f}x<extra></extra>",
        ))
        fig5.update_layout(
            xaxis_title="P&L / Stake",
            yaxis_title=None,
            margin=dict(l=0, r=0, t=10, b=0),
            height=280,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="#2a2d3a"),
        )
        st.plotly_chart(fig5, use_container_width=True)
    else:
        st.info("Stake data not available.")

st.divider()

# ── Summary table by sport ────────────────────────────────────────────────────
st.subheader("Performance Summary by Sport")
sport_summary = (
    df.groupby("sport")
    .agg(
        Trades=("win", "size"),
        Wins=("win", "sum"),
        Losses=("win", lambda x: (~x).sum()),
        **{"Win Rate (%)": ("win", lambda x: round(x.mean() * 100, 1))},
        **{"Total P&L ($)": ("pnl", lambda x: round(x.sum(), 2))},
        **{"Avg P&L ($)": ("pnl", lambda x: round(x.mean(), 2))},
    )
    .reset_index()
    .rename(columns={"sport": "Sport"})
)
st.dataframe(
    sport_summary,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Total P&L ($)": st.column_config.NumberColumn("Total P&L ($)", format="$%.2f"),
        "Avg P&L ($)":   st.column_config.NumberColumn("Avg P&L ($)",   format="$%.2f"),
        "Win Rate (%)":  st.column_config.NumberColumn("Win Rate (%)",  format="%.1f%%"),
    },
)
