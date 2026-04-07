"""
Shared sidebar renderer — call render_sidebar() at the top of every page.

Shows live portfolio stats, bot status indicator, next-scan countdown,
and last-refresh time.
Uses the same cached fetch() so it adds zero extra API calls.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_CST = ZoneInfo("America/Chicago")

import streamlit as st

import sys, os
# sidebar.py lives in streamlit_app/ — utils.py is in the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import fetch, fmt_usd, delete


def _next_scan_label(last_scan_iso: str | None, interval_hours: float) -> str:
    """Return a human-readable 'Next scan in Xh Ym' string, or 'soon'."""
    if not last_scan_iso:
        return "unknown"
    try:
        last = datetime.fromisoformat(last_scan_iso)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        next_scan = last + timedelta(hours=interval_hours)
        delta = next_scan - now
        total_secs = int(delta.total_seconds())
        if total_secs <= 0:
            return "soon"
        h, rem = divmod(total_secs, 3600)
        m = rem // 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "unknown"


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

        # ── Bot status + next scan countdown ───────────────────────────────
        health = fetch("/health")
        if health:
            st.success("🟢  Bot Online", icon=None)
            last_scan = health.get("last_scan_at")
            interval  = float(health.get("scan_interval_hours", 2))
            next_in   = _next_scan_label(last_scan, interval)
            paper     = health.get("paper_trading", False)
            mode_badge = "📄 Paper" if paper else "💸 Live"
            st.caption(f"{mode_badge}  ·  Next scan in **{next_in}**")

            # ── Alert badge ────────────────────────────────────────────────
            error_count   = health.get("error_count", 0)
            warning_count = health.get("warning_count", 0)
            alerts        = health.get("alerts", [])

            if error_count > 0:
                st.markdown(
                    f"""
                    <div style="
                        background:#3d0f0f; border:1px solid #ff4b4b;
                        border-radius:6px; padding:6px 10px; margin:4px 0;
                        display:flex; align-items:center; gap:6px;
                    ">
                        <span style="font-size:1rem;">🚨</span>
                        <span style="color:#ff4b4b; font-weight:600; font-size:.85rem;">
                            {error_count} error{'s' if error_count != 1 else ''}
                            {f"· {warning_count} warning{'s' if warning_count != 1 else ''}" if warning_count else ""}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            elif warning_count > 0:
                st.markdown(
                    f"""
                    <div style="
                        background:#2d2000; border:1px solid #ffa500;
                        border-radius:6px; padding:6px 10px; margin:4px 0;
                        display:flex; align-items:center; gap:6px;
                    ">
                        <span style="font-size:1rem;">⚠️</span>
                        <span style="color:#ffa500; font-weight:600; font-size:.85rem;">
                            {warning_count} warning{'s' if warning_count != 1 else ''}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    """
                    <div style="
                        background:#0d2b1f; border:1px solid #00d4aa;
                        border-radius:6px; padding:6px 10px; margin:4px 0;
                        display:flex; align-items:center; gap:6px;
                    ">
                        <span style="font-size:1rem;">✅</span>
                        <span style="color:#00d4aa; font-weight:600; font-size:.85rem;">
                            No alerts
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # ── Alert detail panel ─────────────────────────────────────────
            if alerts:
                with st.expander(
                    f"{'🚨' if error_count else '⚠️'} View alerts ({len(alerts)})",
                    expanded=False,
                ):
                    for alert in alerts[:10]:   # show at most 10 in sidebar
                        level  = alert.get("level", "warning")
                        msg    = alert.get("message", "")
                        detail = alert.get("detail")
                        source = alert.get("source", "")
                        ts_raw = alert.get("ts", "")
                        # format timestamp to HH:MM CST/CDT
                        try:
                            ts_dt = datetime.fromisoformat(ts_raw).astimezone(_CST)
                            ts_label = ts_dt.strftime("%-I:%M %p")
                        except Exception:
                            ts_label = ts_raw[:5]

                        icon  = "🔴" if level == "error" else "🟡"
                        color = "#ff4b4b" if level == "error" else "#ffa500"
                        st.markdown(
                            f"""
                            <div style="
                                border-left:3px solid {color};
                                padding:4px 8px; margin-bottom:6px;
                                background:#1a1d27; border-radius:0 4px 4px 0;
                            ">
                                <div style="display:flex;justify-content:space-between;align-items:center;">
                                    <span style="color:{color};font-size:.8rem;font-weight:600;">
                                        {icon} {level.upper()}
                                    </span>
                                    <span style="color:#555;font-size:.72rem;">{ts_label}</span>
                                </div>
                                <div style="color:#ccc;font-size:.8rem;margin-top:2px;">{msg}</div>
                                {f'<div style="color:#888;font-size:.72rem;margin-top:2px;">{source}</div>' if source else ''}
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                        if detail:
                            with st.expander("Stack trace", expanded=False):
                                st.code(detail, language="python")

                    if len(alerts) > 10:
                        st.caption(f"… and {len(alerts) - 10} more. Check Settings to clear.")

                    if st.button("✅ Dismiss all alerts", use_container_width=True):
                        delete("/health/alerts")
                        st.cache_data.clear()
                        st.rerun()
        else:
            st.error("🔴  Bot Offline")

        st.divider()

        # ── Live portfolio stats ────────────────────────────────────────────
        portfolio = fetch("/portfolio")
        if portfolio:
            st.markdown("**Portfolio**")

            # Backend pre-computes best available values from Kalshi + DB
            display_balance   = portfolio.get("kalshi_portfolio") or portfolio.get("balance", 0)
            display_available = portfolio.get("available_cash", 0)
            deployed          = portfolio.get("deployed", 0)
            live_indicator    = "" if portfolio.get("kalshi_balance") is not None else " 🔴"

            pnl      = portfolio.get("total_pnl", 0)
            win_rate = portfolio.get("win_rate_pct", 0)
            roi      = portfolio.get("roi_pct", 0)
            active   = portfolio.get("active_trades", 0)

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
                    <span style="color:#888;font-size:.8rem;">Portfolio Value{live_indicator}</span>
                    <span style="font-weight:600;">${display_balance:,.2f}</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Deployed</span>
                    <span style="font-weight:600;">${deployed:,.2f}</span>
                  </div>
                  <div style="display:flex;justify-content:space-between;">
                    <span style="color:#888;font-size:.8rem;">Available Cash</span>
                    <span style="font-weight:600;color:#00d4aa;">${display_available:,.2f}</span>
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
            f"Last refreshed: {datetime.now(_CST).strftime('%-I:%M:%S %p')} CT"
        )
