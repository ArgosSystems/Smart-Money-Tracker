"""
api/services/daily_summary.py
------------------------------
Builds a daily digest of whale activity and broadcasts it via EventDispatcher.

Data sources (existing tables only — no new models):
  whale_alerts          — top moves, volume, leaderboard
  exchange_flow_events  — dominant exchange flow direction

Scheduled task fires at 8 AM UTC every day and dispatches DailySummaryEvent
to all registered BroadcasterPlugins (Twitter, Telegram Channel, Bluesky,
WebSocket push).  Discord auto-push channels also receive it if they subscribe
to the "daily_summary" alert type.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import and_, desc, func, select

from api.events.types import DailySummaryEvent
from api.models import AsyncSessionLocal, ExchangeFlowEvent, WhaleAlert

logger = logging.getLogger(__name__)


# ── DB query helpers ──────────────────────────────────────────────────────────

async def build_daily_summary(since: datetime.datetime | None = None) -> dict:
    """
    Aggregate whale activity for the last 24 h.

    Returns a plain dict mirroring the DailySummaryEvent metadata schema.
    Used by both the scheduler (to dispatch the event) and the REST endpoint.
    """
    now = datetime.datetime.utcnow()
    window_start = since or (now - datetime.timedelta(hours=24))

    async with AsyncSessionLocal() as db:
        # ── Top 5 whale moves by USD value ───────────────────────────────────
        top_result = await db.execute(
            select(
                WhaleAlert.from_address,
                WhaleAlert.to_address,
                WhaleAlert.token_symbol,
                WhaleAlert.amount_usd,
                WhaleAlert.direction,
                WhaleAlert.chain,
            )
            .where(WhaleAlert.detected_at >= window_start)
            .order_by(desc(WhaleAlert.amount_usd))
            .limit(5)
        )
        top_moves = [
            {
                "wallet": row.from_address if row.direction in ("SELL", "SEND") else row.to_address,
                "token":  row.token_symbol or "native",
                "amount_usd": row.amount_usd,
                "direction": row.direction,
                "chain": row.chain,
            }
            for row in top_result.all()
        ]

        # ── Total volume + alert count ─────────────────────────────────────
        agg_result = await db.execute(
            select(
                func.count(WhaleAlert.id).label("cnt"),
                func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("vol"),
            ).where(WhaleAlert.detected_at >= window_start)
        )
        agg_row = agg_result.one()
        alert_count    = agg_row.cnt or 0
        total_vol_usd  = float(agg_row.vol or 0.0)

        # ── Most accumulated token (highest buy count) ─────────────────────
        accum_result = await db.execute(
            select(
                WhaleAlert.token_symbol,
                func.count(WhaleAlert.id).label("buy_count"),
                func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("buy_vol"),
            )
            .where(
                and_(
                    WhaleAlert.detected_at >= window_start,
                    WhaleAlert.direction == "BUY",
                    WhaleAlert.token_symbol.isnot(None),
                )
            )
            .group_by(WhaleAlert.token_symbol)
            .order_by(desc("buy_count"))
            .limit(1)
        )
        accum_row = accum_result.first()
        most_accumulated_token  = accum_row.token_symbol if accum_row else None
        most_accumulated_volume = float(accum_row.buy_vol) if accum_row else 0.0

        # ── Top exchange flow (largest single move) ────────────────────────
        flow_result = await db.execute(
            select(
                ExchangeFlowEvent.flow_direction,
                ExchangeFlowEvent.token_symbol,
                ExchangeFlowEvent.amount_usd,
            )
            .where(ExchangeFlowEvent.fired_at >= window_start)
            .order_by(desc(ExchangeFlowEvent.amount_usd))
            .limit(1)
        )
        flow_row = flow_result.first()
        top_flow_direction  = flow_row.flow_direction if flow_row else None
        top_flow_token      = flow_row.token_symbol   if flow_row else None
        top_flow_amount_usd = float(flow_row.amount_usd) if flow_row else 0.0

    return {
        "date_label":             now.strftime("%Y-%m-%d"),
        "window_hours":           24,
        "generated_at":           now.isoformat(),
        "alert_count_24h":        alert_count,
        "total_volume_24h_usd":   total_vol_usd,
        "top_moves":              top_moves,
        "most_accumulated_token": most_accumulated_token,
        "most_accumulated_volume": most_accumulated_volume,
        "top_flow_direction":     top_flow_direction,
        "top_flow_token":         top_flow_token,
        "top_flow_amount_usd":    top_flow_amount_usd,
    }


async def _dispatch_daily_summary() -> None:
    """Build summary and push via EventDispatcher."""
    from api.events.dispatcher import event_dispatcher  # noqa: PLC0415

    logger.info("DailySummary: building 24 h digest…")
    try:
        meta = await build_daily_summary()
        event = DailySummaryEvent(
            alert_id=0,           # no source table row — synthetic event
            chain="all",
            timestamp=datetime.datetime.utcnow(),
            metadata=meta,
        )
        await event_dispatcher.dispatch(event)
        logger.info(
            "DailySummary dispatched: %d alerts / $%.0f volume",
            meta["alert_count_24h"], meta["total_volume_24h_usd"],
        )
    except Exception as exc:
        logger.error("DailySummary dispatch error: %s", exc)


# ── Scheduled task ────────────────────────────────────────────────────────────

async def _sleep_until_8am_utc() -> None:
    """Sleep until the next 8:00 AM UTC."""
    now = datetime.datetime.utcnow()
    next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= next_8am:
        next_8am += datetime.timedelta(days=1)
    secs = max(1, (next_8am - now).total_seconds())
    logger.info("DailySummaryScheduler: sleeping %.0f s until 08:00 UTC.", secs)
    await asyncio.sleep(secs)


class DailySummaryScheduler:
    """
    Background service: fires DailySummaryEvent at 8 AM UTC every day.
    Started in api/main.py lifespan alongside the other background tasks.
    """

    async def start(self) -> None:
        logger.info("DailySummaryScheduler started.")

        # Optionally run once at startup if it's close to 8 AM (within 5 min)
        now = datetime.datetime.utcnow()
        if now.hour == 8 and now.minute < 5:
            await _dispatch_daily_summary()

        while True:
            try:
                await _sleep_until_8am_utc()
                await _dispatch_daily_summary()
            except asyncio.CancelledError:
                logger.info("DailySummaryScheduler cancelled.")
                return
            except Exception as exc:
                logger.error("DailySummaryScheduler error: %s", exc)
                await asyncio.sleep(3600)  # retry in 1 h on failure
