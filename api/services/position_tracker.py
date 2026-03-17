"""
api/services/position_tracker.py
----------------------------------
Stateless helper that updates WalletPosition rows after each committed
WhaleAlert.

Called by EvmChainScanner._scan_block_range() in the two-phase commit
pattern — after the first db.commit() (which assigns alert.id), before
the event dispatch.

Logic
-----
BUY  → weighted-average cost update:
         new_avg = (old_avg * old_qty + price_per_token * new_qty) / (old_qty + new_qty)
         update total_bought_*, avg_cost_usd

SELL → realize P&L:
         realized = (sell_price_per_token - avg_cost_usd) * amount_token
         update total_sold_*, realized_pnl_usd

SEND → no P&L impact; skip.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import TrackedWallet, WalletPosition, WhaleAlert

logger = logging.getLogger(__name__)

_WINDOW = 24          # hours look-back (unused here, used by accumulation detector)
_MIN_USD  = 50_000.0  # threshold (unused here)


def _token_key(alert: WhaleAlert) -> str:
    """Build a NULL-safe unique key for (chain, token) within a position row."""
    if alert.token_address:
        return alert.token_address.lower()
    symbol = (alert.token_symbol or "UNKNOWN").upper()
    return f"NATIVE:{symbol}"


async def update_position(
    db: AsyncSession,
    alert: WhaleAlert,
    wallet_map: dict[str, TrackedWallet],
) -> Optional[WalletPosition]:
    """
    Upsert a WalletPosition for the wallet that originated `alert`.

    Returns the updated WalletPosition, or None if the direction is SEND
    (no cost-basis impact) or the wallet cannot be identified.
    """
    # SEND (native ETH out of a tracked wallet) is treated as a disposal —
    # same P&L math as SELL.  The whale alert keeps "SEND" for display purposes
    # but the position must record the outflow to show correct realized P&L.
    if alert.direction not in ("BUY", "SELL", "SEND"):
        return None

    # Identify the wallet address that belongs to us
    tracked = wallet_map.get(alert.from_address.lower()) or wallet_map.get(alert.to_address.lower())
    if not tracked:
        return None

    wallet_address = tracked.address.lower()
    token_key      = _token_key(alert)

    # Fetch or create position row
    result = await db.execute(
        select(WalletPosition).where(
            WalletPosition.wallet_address == wallet_address,
            WalletPosition.chain          == alert.chain,
            WalletPosition.token_key      == token_key,
        )
    )
    pos: Optional[WalletPosition] = result.scalar_one_or_none()

    if pos is None:
        pos = WalletPosition(
            wallet_address=wallet_address,
            chain=alert.chain,
            token_key=token_key,
            token_symbol=alert.token_symbol,
            token_address=alert.token_address,
        )
        db.add(pos)

    amount_token = alert.amount_token or 0.0
    amount_usd   = alert.amount_usd   or 0.0

    if amount_token <= 0:
        return pos

    price_per_token = amount_usd / amount_token

    if alert.direction == "BUY":
        old_qty = pos.total_bought_token
        old_avg = pos.avg_cost_usd
        new_qty = old_qty + amount_token
        if new_qty > 0:
            pos.avg_cost_usd = (old_avg * old_qty + price_per_token * amount_token) / new_qty
        pos.total_bought_token += amount_token
        pos.total_bought_usd   += amount_usd

    else:  # SELL or SEND — both are disposals that realize P&L
        realized = (price_per_token - pos.avg_cost_usd) * amount_token
        pos.realized_pnl_usd  += realized
        pos.total_sold_token  += amount_token
        pos.total_sold_usd    += amount_usd

    pos.tx_count += 1

    logger.debug(
        "[position] %s %s %s %s -> pnl=%.2f",
        alert.chain, wallet_address[:8], alert.direction, token_key, pos.realized_pnl_usd
    )
    return pos
