"""
api/services/accumulation_detector.py
----------------------------------------
Detects repeated whale accumulation of the same token within a 24-hour window.

Trigger conditions (all must be true):
  1. wallet has >= 3 BUY alerts for the same token in the last 24 h
  2. total USD volume across those buys >= $50 K
  3. no AccumulationEvent for this wallet+token has fired in the last 6 h  (cooldown)

Called by EvmChainScanner after each whale alert is committed (two-phase commit).
Returns the new AccumulationEvent if the pattern fires, else None.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AccumulationEvent, TrackedWallet, WhaleAlert

logger = logging.getLogger(__name__)

_WINDOW_HOURS   = 24
_MIN_BUY_COUNT  = 3
_MIN_USD        = 50_000.0
_COOLDOWN_HOURS = 6


async def check_accumulation(
    db: AsyncSession,
    alert: WhaleAlert,
    wallet_map: dict[str, TrackedWallet],
) -> Optional[AccumulationEvent]:
    """
    Run the accumulation pattern check after a BUY alert is committed.

    Returns a new AccumulationEvent (already added to session but NOT committed)
    if the pattern fires, or None otherwise.
    """
    if alert.direction != "BUY":
        return None

    tracked = wallet_map.get(alert.from_address.lower()) or wallet_map.get(alert.to_address.lower())
    if not tracked:
        return None

    wallet_address = tracked.address.lower()
    token_address  = alert.token_address
    token_symbol   = alert.token_symbol
    chain          = alert.chain

    window_start = datetime.datetime.utcnow() - datetime.timedelta(hours=_WINDOW_HOURS)

    # Build filter for matching buys of this token by this wallet in the window.
    # On BUY direction the tracked wallet is the *receiver* (to_address), because
    # direction = "SELL" if from_addr in wallet_set else "BUY" — so on a BUY the
    # tracked wallet is the to_address, not the from_address.
    base_filter = and_(
        WhaleAlert.to_address  == wallet_address,
        WhaleAlert.chain       == chain,
        WhaleAlert.direction   == "BUY",
        WhaleAlert.detected_at >= window_start,
    )
    if token_address:
        token_filter = WhaleAlert.token_address == token_address
    else:
        token_filter = WhaleAlert.token_symbol == token_symbol

    # Aggregate: count + sum
    agg = await db.execute(
        select(
            func.count(WhaleAlert.id).label("buy_count"),
            func.sum(WhaleAlert.amount_usd).label("total_usd"),
        ).where(and_(base_filter, token_filter))
    )
    row = agg.one()
    buy_count = row.buy_count or 0
    total_usd = float(row.total_usd or 0.0)

    if buy_count < _MIN_BUY_COUNT or total_usd < _MIN_USD:
        return None

    # Cooldown check — did we already fire recently?
    cooldown_start = datetime.datetime.utcnow() - datetime.timedelta(hours=_COOLDOWN_HOURS)
    cooldown_filter = and_(
        AccumulationEvent.wallet_address == wallet_address,
        AccumulationEvent.chain          == chain,
        AccumulationEvent.fired_at       >= cooldown_start,
    )
    if token_address:
        cooldown_filter = and_(cooldown_filter, AccumulationEvent.token_address == token_address)
    else:
        cooldown_filter = and_(cooldown_filter, AccumulationEvent.token_symbol == token_symbol)

    recent = await db.execute(
        select(AccumulationEvent.id).where(cooldown_filter).limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        logger.debug(
            "[accumulation] cooldown active for %s %s %s",
            chain, wallet_address[:8], token_symbol or token_address
        )
        return None

    # Fire!
    avg_per_tx = total_usd / buy_count if buy_count else 0.0
    event = AccumulationEvent(
        wallet_address=wallet_address,
        chain=chain,
        token_symbol=token_symbol,
        token_address=token_address,
        buy_count=buy_count,
        total_usd=total_usd,
        avg_per_tx_usd=avg_per_tx,
        window_hours=_WINDOW_HOURS,
    )
    db.add(event)

    logger.info(
        "[accumulation] FIRED %s %s %s — %d buys $%.0f",
        chain, wallet_address[:8], token_symbol or token_address, buy_count, total_usd
    )
    return event
