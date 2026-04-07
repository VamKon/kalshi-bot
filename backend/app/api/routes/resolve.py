"""
POST /api/v1/resolve

Checks all open trades against the Kalshi API and closes any whose
market has a final result. Updates portfolio balance accordingly.

Kalshi market lifecycle (production API):
  status: "open"      — betting open
  status: "closed"    — betting closed, awaiting settlement
  status: "finalized" — event ended, trading halted; result field may still
                        be empty for 30–60+ min during Kalshi's settlement pass
  status: "settled"   — result field = "yes" or "no" (fully settled)

Resolution strategy:
  1. Explicit result field — used once Kalshi writes "yes"/"no"
  2. Price convergence fallback — yes_ask/bid converge to $1.00 or $0.00 at
     finalization even before the result field is written; used when status is
     a known final state but result is still empty

Note: The demo/sandbox API does not settle markets.
"""
import logging
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.db_models import Trade
from app.models.schemas import ResolveResult
from app.services.kalshi_client import kalshi_client
from app.services.trading_service import trading_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resolve", tags=["resolve"])

# Kalshi statuses that indicate the market has a final result
SETTLED_STATUSES = {"settled", "finalized", "resolved"}


def _extract_result(market_data: dict) -> str | None:
    """
    Return "yes"/"no" if Kalshi has settled this market, else None.

    Kalshi's production API uses a two-step lifecycle:
      1. status → "finalized": event ended, trading closed, but result field
         may still be empty for 30–60+ minutes during their settlement pass.
      2. result → "yes"|"no": populated after official settlement.

    Primary: check the result field explicitly.
    Fallback: when status is finalized but result is still empty, infer the
    outcome from price convergence — Kalshi sets yes_ask to $1.00 (YES won)
    or $0.00 (YES lost) even before writing the result field.
    """
    status     = (market_data.get("status") or "").lower()
    result_val = (market_data.get("result") or "").strip().lower()
    result_alt = (market_data.get("market_result") or "").strip().lower()

    # Primary: explicit result field (works once Kalshi writes it)
    for val in (result_val, result_alt):
        if val in ("yes", "no"):
            return val

    # Fallback: price convergence for finalized-but-not-yet-result markets.
    # Only applied when the market is in a known finalized state to avoid
    # mis-reading a live market with a temporarily lopsided spread.
    if status in SETTLED_STATUSES:
        try:
            yes_ask = float(market_data.get("yes_ask_dollars") or -1)
            yes_bid = float(market_data.get("yes_bid_dollars") or -1)
            # Use the midpoint so a single missing price doesn't mislead us.
            # Both should be ≥ 0 for this branch to fire.
            if yes_ask >= 0 and yes_bid >= 0:
                mid = (yes_ask + yes_bid) / 2
                if mid >= 0.95:
                    logger.info(
                        "Price-convergence resolution: yes_ask=%.2f yes_bid=%.2f → YES",
                        yes_ask, yes_bid,
                    )
                    return "yes"
                if mid <= 0.05:
                    logger.info(
                        "Price-convergence resolution: yes_ask=%.2f yes_bid=%.2f → NO",
                        yes_ask, yes_bid,
                    )
                    return "no"
        except (TypeError, ValueError):
            pass

    return None


async def run_resolve() -> ResolveResult:
    """
    Core resolution logic — shared by the endpoint and the scan cycle.
    Fetches all open trades, queries Kalshi for each market result,
    and closes trades that have a final outcome.
    """
    trades_checked = 0
    trades_resolved = 0
    wins = 0
    losses = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Trade).where(Trade.status == "open"))
        open_trades = result.scalars().all()
        trades_checked = len(open_trades)
        logger.info("Resolve: checking %d open trade(s)", trades_checked)

        for trade in open_trades:
            try:
                market_data = await kalshi_client.get_market(trade.market_id)
                if not market_data:
                    logger.warning("Market %s not found in Kalshi API", trade.market_id)
                    continue

                result_val = _extract_result(market_data)
                if not result_val:
                    logger.info(
                        "Trade %d [%s] — no result yet (status=%s)",
                        trade.id, trade.market_id,
                        market_data.get("status", "unknown"),
                    )
                    continue

                outcome = "win" if result_val == trade.side.lower() else "loss"
                exit_price = float(
                    market_data.get("yes_ask_dollars") or trade.entry_price
                )

                await trading_service.resolve_trade(
                    db=db,
                    trade_id=trade.id,
                    outcome=outcome,
                    exit_price=exit_price,
                )

                trades_resolved += 1
                if outcome == "win":
                    wins += 1
                else:
                    losses += 1

                logger.info(
                    "Resolved trade %d [%s] side=%s result=%s → %s",
                    trade.id, trade.market_id, trade.side, result_val, outcome,
                )

            except Exception as exc:
                logger.error("Error resolving trade %d (%s): %s",
                             trade.id, trade.market_id, exc)

    logger.info(
        "Resolve complete — %d checked, %d resolved (%d wins / %d losses)",
        trades_checked, trades_resolved, wins, losses,
    )
    return ResolveResult(
        trades_checked=trades_checked,
        trades_resolved=trades_resolved,
        wins=wins,
        losses=losses,
        timestamp=datetime.utcnow(),
    )


@router.post("", response_model=ResolveResult)
async def resolve_trades():
    """
    Check all open trades against Kalshi results and close completed ones.
    Safe to call at any time — no-ops for markets still in progress.
    """
    return await run_resolve()
