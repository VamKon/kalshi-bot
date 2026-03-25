"""
Kalshi Trading Bot — Streamlit Dashboard entry point.

Runs as a multi-page app; each file in pages/ is a separate page.
This file sets up global config (title, layout) and renders the home screen.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from sidebar import render_sidebar

st.set_page_config(
    page_title="Kalshi Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

render_sidebar()

# ── Home / welcome screen ───────────────────────────────────────────────────
st.markdown(
    """
    <div style="text-align:center; padding:40px 0 20px 0;">
        <span style="font-size:4rem;">📈</span>
        <h1 style="margin:8px 0 4px 0;">Kalshi Trading Bot</h1>
        <p style="color:#888; font-size:1.05rem;">
            AI-powered sports prediction market trading &middot; Powered by Claude
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

col1, col2, col3, col4, col5, col6 = st.columns(6)
for col, icon, label in [
    (col1, "📊", "Dashboard"),
    (col2, "📋", "Active Trades"),
    (col3, "📜", "Trade History"),
    (col4, "🏟️", "Markets"),
    (col5, "🔬", "Analytics"),
    (col6, "⚙️",  "Settings"),
]:
    col.markdown(
        f"""
        <div style="
            background:#1a1d27; border-radius:10px;
            padding:20px; text-align:center;
        ">
            <div style="font-size:2rem;">{icon}</div>
            <div style="font-weight:600; margin-top:6px; color:#fafafa;">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)
st.info("👈  Use the **sidebar** to navigate between pages.")
