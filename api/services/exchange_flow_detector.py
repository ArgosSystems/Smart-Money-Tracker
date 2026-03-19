"""
api/services/exchange_flow_detector.py
----------------------------------------
Detects when a tracked whale wallet moves tokens to or from a known exchange.

Signal types
------------
OUTFLOW  — whale sends tokens TO an exchange address   → sell signal 🔴
INFLOW   — whale receives tokens FROM an exchange address → accumulation signal 🟢

Detection logic
---------------
1. From the incoming WhaleAlert, identify which address is the tracked wallet
   and which is the counterpart.
2. Look up the counterpart in SmartLabel where entity_type == 'exchange'.
3. If found, determine flow direction (OUTFLOW or INFLOW).
4. Cooldown: if an ExchangeFlowEvent for the same (wallet, exchange, token)
   already fired within the last hour, skip.
5. Create and return a new ExchangeFlowEvent (added to session, not committed).

Called by EvmChainScanner after each whale alert is committed (two-phase commit).
Returns the new ExchangeFlowEvent if the pattern fires, else None.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import ExchangeFlowEvent, SmartLabel, TrackedWallet, WhaleAlert

logger = logging.getLogger(__name__)

_COOLDOWN_HOURS = 1  # suppress repeated exchange flow events for same trio


async def check_exchange_flow(
    db: AsyncSession,
    alert: WhaleAlert,
    wallet_map: dict[str, TrackedWallet],
) -> Optional[ExchangeFlowEvent]:
    """
    Run the exchange flow detection check after a whale alert is committed.

    Returns a new ExchangeFlowEvent (already added to session but NOT committed)
    if an exchange flow is detected, or None otherwise.
    """
    from_addr = alert.from_address.lower()
    to_addr   = alert.to_address.lower()

    # Determine which end is the tracked whale and which is the counterpart
    if from_addr in wallet_map:
        whale_address    = from_addr
        counterpart_addr = to_addr
        flow_direction   = "OUTFLOW"   # whale → exchange = sell signal 🔴
    elif to_addr in wallet_map:
        whale_address    = to_addr
        counterpart_addr = from_addr
        flow_direction   = "INFLOW"    # exchange → whale = accumulation signal 🟢
    else:
        return None  # shouldn't happen; belt-and-suspenders guard

    # Look up counterpart in SmartLabel — only care about exchange entities
    exchange_label = await db.scalar(
        select(SmartLabel).where(
            and_(
                SmartLabel.address     == counterpart_addr,
                SmartLabel.chain       == alert.chain,
                SmartLabel.entity_type == "exchange",
            )
        )
    )
    if exchange_label is None:
        return None  # counterpart is not a known exchange

    # Cooldown check — avoid firing the same alert repeatedly for rapid transfers
    cooldown_start = datetime.datetime.utcnow() - datetime.timedelta(hours=_COOLDOWN_HOURS)
    cooldown_filter = and_(
        ExchangeFlowEvent.wallet_address   == whale_address,
        ExchangeFlowEvent.exchange_address == counterpart_addr,
        ExchangeFlowEvent.chain            == alert.chain,
        ExchangeFlowEvent.fired_at         >= cooldown_start,
    )
    if alert.token_address:
        cooldown_filter = and_(cooldown_filter, ExchangeFlowEvent.token_address == alert.token_address)
    else:
        cooldown_filter = and_(cooldown_filter, ExchangeFlowEvent.token_symbol == alert.token_symbol)

    recent = await db.execute(
        select(ExchangeFlowEvent.id).where(cooldown_filter).limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        logger.debug(
            "[exchange_flow] cooldown active for %s %s → %s %s",
            alert.chain, whale_address[:8], exchange_label.name,
            alert.token_symbol or alert.token_address
        )
        return None

    # Fire!
    event = ExchangeFlowEvent(
        whale_alert_id   = alert.id,
        wallet_address   = whale_address,
        chain            = alert.chain,
        exchange_name    = exchange_label.name,
        exchange_address = counterpart_addr,
        flow_direction   = flow_direction,
        token_symbol     = alert.token_symbol,
        token_address    = alert.token_address,
        amount_usd       = alert.amount_usd,
        amount_token     = alert.amount_token,
        tx_hash          = alert.tx_hash,
    )
    db.add(event)

    signal = "🔴 OUTFLOW (sell)" if flow_direction == "OUTFLOW" else "🟢 INFLOW (buy)"
    logger.info(
        "[exchange_flow] FIRED %s %s %s → %s %s $%.0f",
        signal, alert.chain, whale_address[:8],
        exchange_label.name, alert.token_symbol or "native", alert.amount_usd
    )
    return event
