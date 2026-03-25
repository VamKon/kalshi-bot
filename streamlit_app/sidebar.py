"""
Shared sidebar renderer — call render_sidebar() at the top of every page.

Shows live portfolio stats, bot status indicator, and last-refresh time.
Uses the same cached fetch() so it adds zero extra API calls.
"""
from datetime import datetime

import streamlit as st

import sys, os
# sidebar.py lives in streamlit_app/ — utils.py is in the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import fetch, fmt_usd


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div style="text-align:center; padding-bottom:8px;">
                <span style="font-size:2rem;">📈</span><br>
                <span style="font-size:1.1rem; font-weight:700; color:#00d4aa;">
                    Kalshi Bot
                </span><br>
                <span style="font-size:0.75rem; color:#888;">
                    Powered by Claude AI
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ── Bot status ─────────────────────────────────────────────────────
        health = fetch("/health")
        if health:
            st.success("🟢  Bot Online", icon=None)
        else:
            st.error("🔴  Bot Offline")

        st.divider()

        # ── Live portfolio stats ────────────────────────────────────────────
        portfolio = fetch("/portfolio")
        if portfolio:
            st.markdown("**Portfolio**")

            balance        = portfolio.get("balance", 0)
            deployed       = portfolio.get("deployed", 0)
            available_cash = portfolio.get("available_cash", balance)
            pnl            = portfolio.get("total_pnl", 0)
            win_rate       = portfolio.get("win_rate_pct", 0)
            roi            = portfolio.get("roi_pct", 0)
            active         = portfolio.get("active_trades", 0)

            pnl_sign  = "+" if pnl >= 0 else ""
            pnl_color = "#00d4aa" if pnl >= 0 else "#ff4b4b"
            roi_color = "#00d4aa" if roi >= 0 else "#ff4b4b"

            st.markdown(
                f"""
                <div style="
                    background:#1a1d27; border-radius:8px; padding:12px 14px;
                    margin-bottom:8px; line-height:1.8;
                ">
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Balance</span>
                    <span style="font-weight:600;">${balance:,.2f}</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Deployed</span>
                    <span style="font-weight:600;">${deployed:,.2f}</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Available Cash</span>
                    <span style="font-weight:600;color:#00d4aa;">${available_cash:,.2f}</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Total P&L</span>
                    <span style="font-weight:600;color:{pnl_color};">
                      {pnl_sign}${abs(pnl):,.2f}
                    </span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">ROI</span>
                    <span style="font-weight:600;color:{roi_color};">{roi:.1f}%</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Win Rate</span>
                    <span style="font-weight:600;">{win_rate:.1f}%</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Open Trades</span>
                    <span style="font-weight:600;">{active}</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.warning("Portfolio unavailable")

        st.divider()

        # ── Navigation hint ────────────────────────────────────────────────
        st.caption("Navigate using the pages above ↑")
        st.caption(
            f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}"
        )
