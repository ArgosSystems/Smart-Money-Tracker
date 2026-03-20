"""
api/routers/insights.py
------------------------
High-value insight endpoints — smart queries on existing DB tables.

Routes
------
GET  /api/v1/insights/daily-summary                     — 24 h whale digest
GET  /api/v1/insights/top-wallets?chain=&limit=10       — top 10 wallets by realized P&L
GET  /api/v1/insights/trending-buys?chain=&hours=4&limit=10 — most bought tokens in window
GET  /api/v1/insights/whale-leaderboard?chain=&limit=10 — top wallets by total volume today
GET  /api/v1/insights/wallet-score/{address}?chain=     — smart wallet accuracy score
"""

from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import Integer, and_, case, desc, func, select, true

from api.models import (
    AsyncSessionLocal,
    BacktestResult,
    ExchangeFlowEvent,
    WalletPosition,
    WhaleAlert,
)
from api.services.daily_summary import build_daily_summary

router = APIRouter(prefix="/api/v1/insights", tags=["Insights"])


# ── /daily-summary ────────────────────────────────────────────────────────────

@router.get("/daily-summary", summary="24 h whale activity digest")
async def daily_summary() -> dict:
    """Return aggregated whale activity for the last 24 hours."""
    return await build_daily_summary()


# ── /top-wallets ──────────────────────────────────────────────────────────────

@router.get("/top-wallets", summary="Top wallets by realized P&L")
async def top_wallets(
    chain: Optional[str] = Query(None, description="Filter by chain"),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """
    Return the top wallets ranked by total realized P&L across all their positions.
    Aggregates wallet_positions rows — one score per unique wallet address.
    """
    async with AsyncSessionLocal() as db:
        filters = []
        if chain:
            filters.append(WalletPosition.chain == chain.lower())

        result = await db.execute(
            select(
                WalletPosition.wallet_address,
                WalletPosition.chain,
                func.sum(WalletPosition.realized_pnl_usd).label("total_pnl"),
                func.sum(WalletPosition.total_bought_usd).label("total_bought"),
                func.sum(WalletPosition.total_sold_usd).label("total_sold"),
                func.sum(WalletPosition.tx_count).label("tx_count"),
                func.count(WalletPosition.id).label("positions"),
            )
            .where(and_(*filters) if filters else true())
            .group_by(WalletPosition.wallet_address, WalletPosition.chain)
            .order_by(desc("total_pnl"))
            .limit(limit)
        )

        return [
            {
                "rank":          i + 1,
                "wallet_address": row.wallet_address,
                "chain":         row.chain,
                "total_pnl_usd": round(row.total_pnl or 0.0, 2),
                "total_bought_usd": round(row.total_bought or 0.0, 2),
                "total_sold_usd":   round(row.total_sold or 0.0, 2),
                "tx_count":      int(row.tx_count or 0),
                "positions":     int(row.positions or 0),
            }
            for i, row in enumerate(result.all())
        ]


# ── /trending-buys ────────────────────────────────────────────────────────────

@router.get("/trending-buys", summary="Most bought tokens in recent window")
async def trending_buys(
    chain: Optional[str] = Query(None, description="Filter by chain"),
    hours: int = Query(4, ge=1, le=24, description="Look-back window in hours"),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """
    Return tokens with the highest buy count from whale_alerts in the last N hours.
    Only BUY direction alerts are counted.
    """
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)

    async with AsyncSessionLocal() as db:
        filters = [
            WhaleAlert.detected_at >= since,
            WhaleAlert.direction == "BUY",
            WhaleAlert.token_symbol.isnot(None),
        ]
        if chain:
            filters.append(WhaleAlert.chain == chain.lower())

        result = await db.execute(
            select(
                WhaleAlert.token_symbol,
                WhaleAlert.chain,
                func.count(WhaleAlert.id).label("buy_count"),
                func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("buy_volume"),
                func.count(func.distinct(WhaleAlert.from_address)).label("unique_buyers"),
            )
            .where(and_(*filters))
            .group_by(WhaleAlert.token_symbol, WhaleAlert.chain)
            .order_by(desc("buy_count"))
            .limit(limit)
        )

        return [
            {
                "rank":          i + 1,
                "token_symbol":  row.token_symbol,
                "chain":         row.chain,
                "buy_count":     int(row.buy_count),
                "buy_volume_usd": round(float(row.buy_volume), 2),
                "unique_buyers": int(row.unique_buyers),
                "window_hours":  hours,
            }
            for i, row in enumerate(result.all())
        ]


# ── /whale-leaderboard ────────────────────────────────────────────────────────

@router.get("/whale-leaderboard", summary="Top wallets by total USD volume today")
async def whale_leaderboard(
    chain: Optional[str] = Query(None, description="Filter by chain"),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """
    Return the wallets that moved the largest total USD amount in the last 24 h.
    Counts both BUY and SELL directions from whale_alerts.
    """
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

    async with AsyncSessionLocal() as db:
        filters = [WhaleAlert.detected_at >= since]
        if chain:
            filters.append(WhaleAlert.chain == chain.lower())

        # Use from_address as the "wallet" (the initiating address)
        result = await db.execute(
            select(
                WhaleAlert.from_address.label("wallet_address"),
                WhaleAlert.chain,
                func.count(WhaleAlert.id).label("tx_count"),
                func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("total_volume"),
                func.sum(
                    case((WhaleAlert.direction == "BUY", WhaleAlert.amount_usd), else_=0.0)
                ).label("buy_volume"),
                func.sum(
                    case((WhaleAlert.direction == "SELL", WhaleAlert.amount_usd), else_=0.0)
                ).label("sell_volume"),
            )
            .where(and_(*filters))
            .group_by(WhaleAlert.from_address, WhaleAlert.chain)
            .order_by(desc("total_volume"))
            .limit(limit)
        )

        return [
            {
                "rank":           i + 1,
                "wallet_address": row.wallet_address,
                "chain":          row.chain,
                "tx_count":       int(row.tx_count),
                "total_volume_usd": round(float(row.total_volume), 2),
                "buy_volume_usd":  round(float(row.buy_volume or 0), 2),
                "sell_volume_usd": round(float(row.sell_volume or 0), 2),
            }
            for i, row in enumerate(result.all())
        ]


# ── /wallet-score ─────────────────────────────────────────────────────────────

@router.get("/wallet-score/{address}", summary="Smart wallet accuracy score")
async def wallet_score(
    address: str,
    chain: Optional[str] = Query(None, description="Filter by chain"),
) -> dict:
    """
    Compute a smart wallet score combining BacktestResult accuracy + whale_alerts volume.

    Returns:
      win_rate_pct        — % of signals that were directionally correct (72 h)
      avg_pnl_72h_pct     — average P&L at 72 h across all backtest rows
      total_signals       — total signals processed in backtest
      total_volume_usd    — total whale move volume by this wallet
      tx_count            — total transactions in whale_alerts
      best_trade_pct      — highest individual pnl_72h_pct
      worst_trade_pct     — lowest individual pnl_72h_pct
    """
    addr = address.lower()

    async with AsyncSessionLocal() as db:
        # ── Backtest accuracy ──────────────────────────────────────────────
        bt_filters = [
            BacktestResult.wallet_address == addr,
            BacktestResult.was_correct_72h.isnot(None),
        ]
        if chain:
            bt_filters.append(BacktestResult.chain == chain.lower())

        bt_agg = await db.execute(
            select(
                func.count(BacktestResult.id).label("total"),
                func.sum(BacktestResult.was_correct_72h.cast(Integer)).label("correct"),
                func.avg(BacktestResult.pnl_72h_pct).label("avg_pnl"),
                func.max(BacktestResult.pnl_72h_pct).label("best"),
                func.min(BacktestResult.pnl_72h_pct).label("worst"),
            ).where(and_(*bt_filters))
        )
        bt_row = bt_agg.one()
        total_signals = int(bt_row.total or 0)
        total_correct = int(bt_row.correct or 0)
        win_rate      = round(total_correct / total_signals * 100, 1) if total_signals > 0 else None
        avg_pnl       = round(float(bt_row.avg_pnl), 2) if bt_row.avg_pnl is not None else None
        best_trade    = round(float(bt_row.best), 2)   if bt_row.best  is not None else None
        worst_trade   = round(float(bt_row.worst), 2)  if bt_row.worst is not None else None

        # ── Whale move volume ──────────────────────────────────────────────
        wa_filters = [WhaleAlert.from_address == addr]
        if chain:
            wa_filters.append(WhaleAlert.chain == chain.lower())

        wa_agg = await db.execute(
            select(
                func.count(WhaleAlert.id).label("tx_count"),
                func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("total_vol"),
            ).where(and_(*wa_filters))
        )
        wa_row = wa_agg.one()
        tx_count     = int(wa_row.tx_count or 0)
        total_vol    = round(float(wa_row.total_vol or 0.0), 2)

    return {
        "wallet_address":    address,
        "chain":             chain or "all",
        "total_signals":     total_signals,
        "total_correct":     total_correct,
        "win_rate_pct":      win_rate,
        "avg_pnl_72h_pct":  avg_pnl,
        "best_trade_pct":    best_trade,
        "worst_trade_pct":   worst_trade,
        "tx_count":          tx_count,
        "total_volume_usd":  total_vol,
        "has_backtest_data": total_signals > 0,
    }
