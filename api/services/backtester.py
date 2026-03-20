"""
api/services/backtester.py
---------------------------
Backtesting engine — evaluates historical accuracy of Smart Money Tracker signals.

For every AccumulationEvent and ExchangeFlowEvent in the database, it fetches
the token price at signal time and at +24h / +72h / +7d using the DeFiLlama
historical price API (same source used by PriceAlertChecker).

Results are stored in BacktestResult and aggregated into win-rate / avg-P&L
stats per signal type for the /bot_stats command and landing page.

Correctness definition (directionally-aware):
  - accumulation          → bullish: was_correct = price_72h > signal_price * 1.05
  - exchange_flow INFLOW  → bullish: was_correct = price_72h > signal_price * 1.05
  - exchange_flow OUTFLOW → bearish: was_correct = price_72h < signal_price * 0.95

Background task: runs daily at UTC midnight to fill in new results.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

import httpx
from sqlalchemy import Integer, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import (
    AccumulationEvent,
    AsyncSessionLocal,
    BacktestResult,
    ExchangeFlowEvent,
)

logger = logging.getLogger(__name__)

_DEFILLAMA_HIST = "https://coins.llama.fi/prices/historical"

# How long to wait between DeFiLlama historical price requests during backfill.
# DeFiLlama is free with no rate limits, but we be respectful.
_REQUEST_DELAY = 0.05   # seconds
_CORRECT_THRESHOLD = 5.0  # percent — "price moved >5% in predicted direction"


# ── DeFiLlama historical price fetch ─────────────────────────────────────────

async def fetch_historical_price(
    token_address: str,
    chain: str,
    at: datetime.datetime,
    client: httpx.AsyncClient,
) -> float:
    """Return the DeFiLlama price for token_address:chain closest to `at`."""
    coin_key = f"{chain}:{token_address.lower()}"
    unix_ts  = int(at.timestamp())
    url      = f"{_DEFILLAMA_HIST}/{unix_ts}/{coin_key}"
    try:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json().get("coins", {}).get(coin_key, {}).get("price", 0.0)
    except Exception as exc:
        logger.debug("Historical price miss %s@%s: %s", coin_key, unix_ts, exc)
        return 0.0


def _pct(base: float, later: float) -> Optional[float]:
    """Percentage change from base to later.  Returns None if base is zero."""
    if base == 0.0:
        return None
    return round((later - base) / base * 100, 4)


def _is_correct(signal_type: str, flow_direction: Optional[str], pnl_pct: Optional[float]) -> Optional[bool]:
    """
    Determine whether a signal was directionally correct.

    accumulation / exchange_flow INFLOW → bullish → correct if price rose > 5%
    exchange_flow OUTFLOW               → bearish → correct if price fell > 5%
    """
    if pnl_pct is None:
        return None
    if signal_type == "exchange_flow" and flow_direction == "OUTFLOW":
        return pnl_pct < -_CORRECT_THRESHOLD
    return pnl_pct > _CORRECT_THRESHOLD


# ── Core backfill logic ───────────────────────────────────────────────────────

async def _process_accumulation_events(db: AsyncSession, client: httpx.AsyncClient) -> int:
    """Insert BacktestResult rows for AccumulationEvents not yet processed."""
    now = datetime.datetime.utcnow()

    # Load already-processed IDs
    existing = set(
        row[0]
        for row in (await db.execute(
            select(BacktestResult.source_event_id).where(
                BacktestResult.signal_type == "accumulation"
            )
        )).all()
    )

    # Load all accumulation events that have a token address to price
    result = await db.execute(
        select(AccumulationEvent).where(
            AccumulationEvent.token_address.isnot(None)
        ).order_by(AccumulationEvent.fired_at)
    )
    events: list[AccumulationEvent] = list(result.scalars().all())

    inserted = 0
    for event in events:
        if event.id in existing:
            continue

        # We need at least 24h to have elapsed to have a meaningful data point
        if now < event.fired_at + datetime.timedelta(hours=24):
            continue

        # Fetch price at signal time
        signal_price = await fetch_historical_price(
            event.token_address, event.chain, event.fired_at, client
        )
        await asyncio.sleep(_REQUEST_DELAY)

        if signal_price == 0.0:
            # No price data — skip this signal
            continue

        # +24h price
        at_24h  = event.fired_at + datetime.timedelta(hours=24)
        price_24h = await fetch_historical_price(
            event.token_address, event.chain, at_24h, client
        ) if now >= at_24h else None
        await asyncio.sleep(_REQUEST_DELAY)

        # +72h price
        at_72h  = event.fired_at + datetime.timedelta(hours=72)
        price_72h = await fetch_historical_price(
            event.token_address, event.chain, at_72h, client
        ) if now >= at_72h else None
        await asyncio.sleep(_REQUEST_DELAY)

        # +7d price
        at_7d   = event.fired_at + datetime.timedelta(days=7)
        price_7d  = await fetch_historical_price(
            event.token_address, event.chain, at_7d, client
        ) if now >= at_7d else None
        await asyncio.sleep(_REQUEST_DELAY)

        # Convert 0.0 from DeFiLlama to None (no data)
        price_24h = price_24h if price_24h else None
        price_72h = price_72h if price_72h else None
        price_7d  = price_7d  if price_7d  else None

        pnl_24h = _pct(signal_price, price_24h) if price_24h else None
        pnl_72h = _pct(signal_price, price_72h) if price_72h else None
        pnl_7d  = _pct(signal_price, price_7d)  if price_7d  else None

        row = BacktestResult(
            signal_type     = "accumulation",
            flow_direction  = None,
            wallet_address  = event.wallet_address,
            chain           = event.chain,
            token_address   = event.token_address,
            token_symbol    = event.token_symbol,
            source_event_id = event.id,
            signal_fired_at = event.fired_at,
            signal_price_usd= signal_price,
            price_24h_usd   = price_24h,
            price_72h_usd   = price_72h,
            price_7d_usd    = price_7d,
            pnl_24h_pct     = pnl_24h,
            pnl_72h_pct     = pnl_72h,
            pnl_7d_pct      = pnl_7d,
            was_correct_24h = _is_correct("accumulation", None, pnl_24h),
            was_correct_72h = _is_correct("accumulation", None, pnl_72h),
        )
        db.add(row)
        try:
            await db.commit()
            inserted += 1
            logger.info(
                "Backtest [accumulation] event=%d %s %s signal=$%.4f pnl72h=%s",
                event.id, event.chain, event.token_symbol, signal_price,
                f"{pnl_72h:+.1f}%" if pnl_72h is not None else "pending",
            )
        except IntegrityError:
            await db.rollback()  # Already inserted by a concurrent run

    return inserted


async def _process_exchange_flow_events(db: AsyncSession, client: httpx.AsyncClient) -> int:
    """Insert BacktestResult rows for ExchangeFlowEvents not yet processed."""
    now = datetime.datetime.utcnow()

    existing = set(
        row[0]
        for row in (await db.execute(
            select(BacktestResult.source_event_id).where(
                BacktestResult.signal_type == "exchange_flow"
            )
        )).all()
    )

    result = await db.execute(
        select(ExchangeFlowEvent).where(
            ExchangeFlowEvent.token_address.isnot(None)
        ).order_by(ExchangeFlowEvent.fired_at)
    )
    events: list[ExchangeFlowEvent] = list(result.scalars().all())

    inserted = 0
    for event in events:
        if event.id in existing:
            continue

        if now < event.fired_at + datetime.timedelta(hours=24):
            continue

        signal_price = await fetch_historical_price(
            event.token_address, event.chain, event.fired_at, client
        )
        await asyncio.sleep(_REQUEST_DELAY)

        if signal_price == 0.0:
            continue

        at_24h = event.fired_at + datetime.timedelta(hours=24)
        price_24h = await fetch_historical_price(
            event.token_address, event.chain, at_24h, client
        ) if now >= at_24h else None
        await asyncio.sleep(_REQUEST_DELAY)

        at_72h = event.fired_at + datetime.timedelta(hours=72)
        price_72h = await fetch_historical_price(
            event.token_address, event.chain, at_72h, client
        ) if now >= at_72h else None
        await asyncio.sleep(_REQUEST_DELAY)

        at_7d = event.fired_at + datetime.timedelta(days=7)
        price_7d = await fetch_historical_price(
            event.token_address, event.chain, at_7d, client
        ) if now >= at_7d else None
        await asyncio.sleep(_REQUEST_DELAY)

        price_24h = price_24h if price_24h else None
        price_72h = price_72h if price_72h else None
        price_7d  = price_7d  if price_7d  else None

        pnl_24h = _pct(signal_price, price_24h) if price_24h else None
        pnl_72h = _pct(signal_price, price_72h) if price_72h else None
        pnl_7d  = _pct(signal_price, price_7d)  if price_7d  else None

        direction = event.flow_direction  # "INFLOW" | "OUTFLOW"

        row = BacktestResult(
            signal_type     = "exchange_flow",
            flow_direction  = direction,
            wallet_address  = event.wallet_address,
            chain           = event.chain,
            token_address   = event.token_address,
            token_symbol    = event.token_symbol,
            source_event_id = event.id,
            signal_fired_at = event.fired_at,
            signal_price_usd= signal_price,
            price_24h_usd   = price_24h,
            price_72h_usd   = price_72h,
            price_7d_usd    = price_7d,
            pnl_24h_pct     = pnl_24h,
            pnl_72h_pct     = pnl_72h,
            pnl_7d_pct      = pnl_7d,
            was_correct_24h = _is_correct("exchange_flow", direction, pnl_24h),
            was_correct_72h = _is_correct("exchange_flow", direction, pnl_72h),
        )
        db.add(row)
        try:
            await db.commit()
            inserted += 1
            logger.info(
                "Backtest [exchange_flow] event=%d %s %s %s signal=$%.4f pnl72h=%s",
                event.id, direction, event.chain, event.token_symbol, signal_price,
                f"{pnl_72h:+.1f}%" if pnl_72h is not None else "pending",
            )
        except IntegrityError:
            await db.rollback()

    return inserted


async def backfill_results() -> dict:
    """
    Process all AccumulationEvent and ExchangeFlowEvent records not yet in BacktestResult.
    Returns a summary dict with counts inserted per signal type.
    """
    logger.info("Backtest backfill started…")
    async with AsyncSessionLocal() as db:
        async with httpx.AsyncClient() as client:
            acc  = await _process_accumulation_events(db, client)
            exfl = await _process_exchange_flow_events(db, client)

    summary = {"accumulation_inserted": acc, "exchange_flow_inserted": exfl, "total": acc + exfl}
    logger.info("Backtest backfill complete: %s", summary)
    return summary


# ── Stats aggregation ─────────────────────────────────────────────────────────

async def get_stats() -> dict:
    """
    Return win rate and average P&L per signal type.

    Only rows with was_correct_72h populated (i.e. 72h has elapsed) count
    towards the win rate — earlier results are excluded from the denominator.
    """
    async with AsyncSessionLocal() as db:
        stats: dict = {}

        for sig_type in ("accumulation", "exchange_flow"):
            # All rows with 72h data available
            result = await db.execute(
                select(
                    func.count(BacktestResult.id).label("total"),
                    func.sum(
                        BacktestResult.was_correct_72h.cast(Integer)
                    ).label("correct"),
                    func.avg(BacktestResult.pnl_72h_pct).label("avg_pnl_72h"),
                    func.avg(BacktestResult.pnl_24h_pct).label("avg_pnl_24h"),
                    func.avg(BacktestResult.pnl_7d_pct).label("avg_pnl_7d"),
                ).where(
                    and_(
                        BacktestResult.signal_type == sig_type,
                        BacktestResult.was_correct_72h.isnot(None),
                    )
                )
            )
            row = result.one()
            total   = row.total or 0
            correct = int(row.correct or 0)

            stats[sig_type] = {
                "total_signals"  : total,
                "total_correct"  : correct,
                "win_rate_pct"   : round(correct / total * 100, 1) if total > 0 else 0.0,
                "avg_pnl_72h_pct": round(row.avg_pnl_72h or 0.0, 2),
                "avg_pnl_24h_pct": round(row.avg_pnl_24h or 0.0, 2),
                "avg_pnl_7d_pct" : round(row.avg_pnl_7d  or 0.0, 2),
            }

        stats["generated_at"] = datetime.datetime.utcnow().isoformat()
        return stats


# ── Background task (daily midnight UTC) ─────────────────────────────────────

async def _sleep_until_midnight() -> None:
    """Sleep until the next UTC midnight."""
    now      = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    secs = max(1, (tomorrow - now).total_seconds())
    logger.info("Backtester daily task sleeping %.0f seconds until UTC midnight.", secs)
    await asyncio.sleep(secs)


class Backtester:
    """
    Background service that runs a backfill at UTC midnight every day.
    Started in api/main.py lifespan alongside the other background tasks.
    """

    async def start(self) -> None:
        logger.info("Backtester started — daily refresh at UTC midnight.")
        # Run once on startup to pick up any existing events
        try:
            await backfill_results()
        except Exception as exc:
            logger.error("Backtester initial backfill error: %s", exc)

        while True:
            try:
                await _sleep_until_midnight()
                await backfill_results()
            except asyncio.CancelledError:
                logger.info("Backtester cancelled.")
                return
            except Exception as exc:
                logger.error("Backtester daily run error: %s", exc)
                await asyncio.sleep(3600)  # retry in 1h on failure
