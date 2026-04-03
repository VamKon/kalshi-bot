"""
Page 5 — Settings
Configure scan interval, Kelly fraction, max trade size, min confidence.
Changes are applied in-memory in the backend (restart to reset).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils import fetch, patch, post, put
from sidebar import render_sidebar

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")

render_sidebar()
st.title("⚙️ Bot Settings")
st.caption("Changes take effect immediately but reset on backend restart. Set env vars for persistence.")

current = fetch("/settings")
if not current:
    st.warning("Cannot reach backend — is it running?")
    st.stop()

st.divider()

with st.form("settings_form"):
    st.subheader("Sports to Scan")
    all_sports = current.get("all_sports", ["NFL", "NBA", "MLS", "Cricket"])
    current_sports = current.get("monitored_sports", all_sports)
    selected_sports = st.multiselect(
        "Active Sports",
        options=all_sports,
        default=current_sports,
        help="Only these sports will be scanned for trade opportunities. Takes effect on the next scan.",
    )
    if not selected_sports:
        st.warning("⚠️ Select at least one sport — the bot won't scan anything otherwise.")

    st.subheader("Market Type Filter")
    game_winner_only = st.toggle(
        "Game-winner markets only",
        value=bool(current.get("game_winner_only", True)),
        help=(
            "When ON, only bet on straight win/loss markets (who wins the game). "
            "When OFF, also consider spreads, totals, first-half, and prop markets."
        ),
    )
    if not game_winner_only:
        st.caption("⚠️ All market types enabled — more opportunities but higher noise.")

    st.subheader("Scheduler")
    scan_hours = st.number_input(
        "Scan Interval (hours)",
        min_value=1, max_value=168,
        value=int(current.get("scan_interval_hours", 12)),
        help="How often the bot scans Kalshi for new opportunities.",
    )

    st.subheader("Trade Sizing (Kelly Criterion)")
    kelly = st.slider(
        "Kelly Fraction",
        min_value=0.05, max_value=1.0, step=0.05,
        value=float(current.get("kelly_fraction", 0.25)),
        help="Multiplier applied to full Kelly size. 0.25 = quarter Kelly (recommended).",
    )
    sz_col1, sz_col2 = st.columns(2)
    max_trade = sz_col1.number_input(
        "Max Stake per Trade ($)",
        min_value=1.0, max_value=10_000.0, step=1.0,
        value=float(current.get("max_trade_usd", 11.0)),
        help="Hard dollar cap per trade. Kelly output is clipped to this value.",
    )
    max_trade_pct = sz_col2.number_input(
        "Max Stake (% of bankroll)",
        min_value=1, max_value=100, step=1,
        value=int(round(float(current.get("max_trade_pct", 0.10)) * 100)),
        help="Hard percentage cap per trade. The lower of this and Max Stake ($) wins.",
    )

    st.subheader("AI Decision Threshold")
    min_conf = st.slider(
        "Minimum Confidence to Trade",
        min_value=0.50, max_value=0.95, step=0.01,
        value=float(current.get("min_confidence", 0.60)),
        help="Claude must be at least this confident before placing a trade.",
    )

    submitted = st.form_submit_button("💾 Save Settings", use_container_width=True)

if submitted:
    if not selected_sports:
        st.error("Cannot save — please select at least one sport.")
    else:
        result = patch(
            "/settings",
            json={
                "scan_interval_hours": scan_hours,
                "kelly_fraction": kelly,
                "max_trade_usd": max_trade,
                "max_trade_pct": max_trade_pct / 100.0,
                "min_confidence": min_conf,
                "monitored_sports": selected_sports,
                "game_winner_only": game_winner_only,
            },
        )
        if result:
            st.success(
                f"Settings updated! Scanning: **{', '.join(result.get('monitored_sports', selected_sports))}**"
            )
            st.cache_data.clear()

st.divider()

# ── Portfolio Balance ───────────────────────────────────────────────────────
st.subheader("💰 Portfolio Balance")

portfolio = fetch("/portfolio")
if portfolio:
    # Backend pre-computes best available values from Kalshi + DB
    display_total     = portfolio.get("kalshi_portfolio") or portfolio.get("balance", 0.0)
    display_available = portfolio.get("available_cash", 0.0)
    deployed          = portfolio.get("deployed", 0.0)

    bc1, bc2, bc3 = st.columns(3)
    bc1.metric(
        "Portfolio Value",
        f"${display_total:,.2f}",
        help="Live total from Kalshi (cash + open position value). Falls back to DB if Kalshi unreachable.",
    )
    bc2.metric(
        "Deployed in Trades",
        f"${deployed:,.2f}",
        help="Sum of stakes currently locked in open trades (from DB)",
    )
    bc3.metric(
        "Available Cash",
        f"${display_available:,.2f}",
        help="Live cash balance from Kalshi — ready to place new trades.",
    )

    st.markdown("**Adjust Balance**")
    st.caption(
        "Set the portfolio balance directly — useful for topping up the paper bankroll "
        "or resetting after testing."
    )

    adj_col1, adj_col2 = st.columns([2, 1])
    new_balance = adj_col1.number_input(
        "New Balance ($)",
        min_value=0.01, max_value=1_000_000.0, step=10.0,
        value=round(display_total, 2),
        format="%.2f",
        help="Enter the new total portfolio balance and click Update.",
    )
    update_clicked = adj_col2.button("💾 Update Balance", use_container_width=True)

    if update_clicked:
        if abs(new_balance - display_total) < 0.01:
            st.info("Balance unchanged.")
        else:
            result = put("/portfolio/balance", json={"balance": new_balance})
            if result:
                old_b = result.get("old_balance", balance)
                new_b = result.get("new_balance", new_balance)
                direction = "increased" if new_b > old_b else "decreased"
                st.success(
                    f"Balance {direction} from **${old_b:,.2f}** → **${new_b:,.2f}**"
                )
                st.cache_data.clear()
                st.rerun()
else:
    st.warning("Could not load portfolio data.")

st.divider()

# ── Manual actions ─────────────────────────────────────────────────────────
st.subheader("Manual Actions")
col1, col2 = st.columns(2)

with col1:
    st.markdown("**Trigger Market Scan**")
    st.caption("Kicks off a background scan across all monitored sports (takes 1–3 min).")
    if st.button("🔍 Trigger Scan", use_container_width=True):
        result = post("/scan")
        if result:
            status = result.get("status", "")
            if status == "started":
                st.success(
                    "✅ Scan started in the background. "
                    "Check the Active Trades page in 1–3 minutes for results."
                )
            else:
                # Backwards-compat: old sync response with markets_scanned
                st.success(
                    f"Scan complete — {result.get('markets_scanned', 0)} markets scanned, "
                    f"{result.get('trades_placed', 0)} trade(s) placed."
                )
        else:
            st.error("Scan failed — check backend logs.")

with col2:
    st.markdown("**Resolve Completed Trades**")
    st.caption("Checks all open trades against Kalshi results and closes any that have a final outcome.")
    if st.button("✅ Resolve Completed Trades", use_container_width=True):
        with st.spinner("Checking Kalshi for results…"):
            result = post("/resolve")
        if result:
            resolved = result.get("trades_resolved", 0)
            checked = result.get("trades_checked", 0)
            wins = result.get("wins", 0)
            losses = result.get("losses", 0)
            if resolved == 0:
                st.info(f"Checked {checked} open trade(s) — none have resolved yet.")
            else:
                st.success(
                    f"Resolved {resolved} trade(s) — {wins} win(s), {losses} loss(es)."
                )
            st.cache_data.clear()
        else:
            st.error("Resolve failed — check backend logs.")

st.divider()

# ── Current live configuration (read-only summary) ─────────────────────────
st.subheader("📋 Active Configuration")
st.caption("What the bot is currently running with. Edit above and save to change.")

cfg1, cfg2, cfg3, cfg4, cfg5, cfg6 = st.columns(6)
cfg1.metric(
    "Scan Interval",
    f"{current.get('scan_interval_hours', '?')}h",
    help="How often the bot scans for opportunities",
)
cfg2.metric(
    "Kelly Fraction",
    f"{float(current.get('kelly_fraction', 0.25)):.0%}",
    help="Fraction of full Kelly bet size applied to each trade",
)
cfg3.metric(
    "Max Stake",
    f"${float(current.get('max_trade_usd', 11)):.0f}  /  {float(current.get('max_trade_pct', 0.10)):.0%}",
    help="Hard cap per trade: lower of the $ amount and the % of bankroll wins",
)
cfg4.metric(
    "Min Confidence",
    f"{float(current.get('min_confidence', 0.50)):.0%}",
    help="Minimum AI confidence required to place a trade",
)
cfg5.metric(
    "Min Edge",
    f"{float(current.get('min_edge_threshold', 0.02)):.0%}",
    help="Minimum edge (consensus prob − Kalshi price) required to trade",
)
cfg6.metric(
    "Mode",
    "📄 Paper" if current.get("paper_trading", True) else "💸 Live",
    help="Whether the bot places real money trades or simulates them",
)

st.markdown("")
market_type_label = "Game-winner only" if current.get("game_winner_only", True) else "All market types"
sports_list       = ", ".join(current.get("monitored_sports", [])) or "none"
kalshi_url_label  = current.get("kalshi_api_base_url", "—")

info_col1, info_col2 = st.columns(2)
info_col1.info(f"**Sports:** {sports_list}  \n**Market filter:** {market_type_label}")
info_col2.info(
    f"**Kalshi API:** `{kalshi_url_label}`  \n"
    f"**Max bid-ask spread:** {float(current.get('max_bid_ask_spread', 0.04)):.0%}  ·  "
    f"**Min volume:** {int(float(current.get('min_market_volume', 100)))}"
)
