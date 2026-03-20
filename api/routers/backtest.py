"""
api/routers/backtest.py
------------------------
Backtesting endpoints — signal accuracy stats and raw results.

Routes
------
GET  /api/v1/backtest/stats    — win rate + avg P&L per signal type
GET  /api/v1/backtest/results  — paginated BacktestResult rows
POST /api/v1/backtest/run      — trigger manual backfill
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import BacktestResult, get_db
from api.services.backtester import backfill_results, get_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/backtest", tags=["Backtesting"])

_backfill_lock = asyncio.Lock()


@router.get("/stats", summary="Signal accuracy statistics")
async def backtest_stats() -> dict:
    """
    Return win rate and average P&L for accumulation and exchange_flow signals.

    Only signals where 72 h have elapsed count towards the win rate.
    Suitable for the landing page proof-of-accuracy section.
    """
    return await get_stats()


@router.get("/results", summary="Paginated backtest results")
async def backtest_results(
    signal_type: Optional[str] = Query(None, description="Filter: accumulation | exchange_flow"),
    chain: Optional[str] = Query(None, description="Filter by chain"),
    was_correct: Optional[bool] = Query(None, description="Filter by correctness"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return BacktestResult rows, newest first."""
    query = select(BacktestResult).order_by(desc(BacktestResult.signal_fired_at))

    if signal_type:
        query = query.where(BacktestResult.signal_type == signal_type.lower())
    if chain:
        query = query.where(BacktestResult.chain == chain.lower())
    if was_correct is not None:
        query = query.where(BacktestResult.was_correct_72h == was_correct)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    rows   = list(result.scalars().all())

    return [
        {
            "id"             : r.id,
            "signal_type"    : r.signal_type,
            "flow_direction" : r.flow_direction,
            "wallet_address" : r.wallet_address,
            "chain"          : r.chain,
            "token_address"  : r.token_address,
            "token_symbol"   : r.token_symbol,
            "signal_fired_at": r.signal_fired_at.isoformat() if r.signal_fired_at else None,
            "signal_price_usd": r.signal_price_usd,
            "price_24h_usd"  : r.price_24h_usd,
            "price_72h_usd"  : r.price_72h_usd,
            "price_7d_usd"   : r.price_7d_usd,
            "pnl_24h_pct"    : r.pnl_24h_pct,
            "pnl_72h_pct"    : r.pnl_72h_pct,
            "pnl_7d_pct"     : r.pnl_7d_pct,
            "was_correct_24h": r.was_correct_24h,
            "was_correct_72h": r.was_correct_72h,
            "processed_at"   : r.processed_at.isoformat() if r.processed_at else None,
        }
        for r in rows
    ]


@router.post("/run", summary="Trigger manual backfill", status_code=202)
async def run_backfill() -> dict:
    """
    Trigger a manual backfill of BacktestResult rows.

    Processes all AccumulationEvent and ExchangeFlowEvent records not yet
    in backtest_results.  Returns 202 immediately if a backfill is already
    running (protected by a lock to prevent double-runs).
    """
    if _backfill_lock.locked():
        return {"status": "already_running", "message": "A backfill is already in progress."}

    async def _run() -> None:
        async with _backfill_lock:
            try:
                await backfill_results()
            except Exception as exc:
                logger.error("Manual backfill error: %s", exc)

    asyncio.create_task(_run(), name="manual_backfill")
    return {"status": "started", "message": "Backfill started in the background."}
