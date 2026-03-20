"""
api/services/cross_chain_entity.py
------------------------------------
Cross-chain entity detection service.

Links known addresses of the same real-world entity across all supported
chains using the SmartLabel table.  Answers the question:

    "What is Jump Trading doing across ALL chains right now?"

Entity resolution
-----------------
SmartLabel rows like "Binance 14", "Binance 15", "Binance Hot Wallet" all
map to the entity **Binance**.  We normalise names by stripping trailing
numeric suffixes and parenthetical qualifiers:

    "Jump Trading 2"         → "Jump Trading"
    "Binance 10 (Cold)"     → "Binance"
    "Coinbase Prime"         → "Coinbase Prime"   (no trailing number)
    "Vitalik Buterin"        → "Vitalik Buterin"

All queries hit the DB directly — no background task needed.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from sqlalchemy import and_, case, desc, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import (
    AsyncSessionLocal,
    ExchangeFlowEvent,
    SmartLabel,
    WalletPosition,
    WhaleAlert,
)

# ── Entity name normalisation ────────────────────────────────────────────────

_TRAILING_PAREN = re.compile(r"\s*\(.*?\)\s*$")
_TRAILING_NUM = re.compile(r"\s+\d+$")


def normalize_entity_name(name: str) -> str:
    """
    Strip trailing parenthetical qualifiers and numeric suffixes to produce
    a canonical entity group key.

    Examples
    --------
    >>> normalize_entity_name("Binance 14")
    'Binance'
    >>> normalize_entity_name("Binance 10 (Cold)")
    'Binance'
    >>> normalize_entity_name("Coinbase Prime")
    'Coinbase Prime'
    >>> normalize_entity_name("Jump Trading 2")
    'Jump Trading'
    """
    n = _TRAILING_PAREN.sub("", name).strip()
    n = _TRAILING_NUM.sub("", n).strip()
    return n


# ── Address resolution ───────────────────────────────────────────────────────

async def get_entity_addresses(
    db: AsyncSession, entity_name: str
) -> list[SmartLabel]:
    """
    Return all SmartLabel rows whose normalised name matches `entity_name`.

    Uses LIKE patterns: "Jump Trading" matches "Jump Trading",
    "Jump Trading 2", "Jump Trading (Cold)", etc.
    """
    normalised = normalize_entity_name(entity_name)

    result = await db.execute(select(SmartLabel))
    all_labels = list(result.scalars().all())

    return [
        lbl for lbl in all_labels
        if normalize_entity_name(lbl.name) == normalised
    ]


async def resolve_entity_from_address(
    db: AsyncSession, address: str, chain: Optional[str] = None
) -> Optional[dict]:
    """
    Given any address, find its SmartLabel entity and return the full
    cross-chain profile.

    Returns None if the address is not in the SmartLabel table.

    Response shape:
    {
        "entity_name": "Binance",
        "entity_type": "exchange",
        "addresses": [
            {"address": "0x28c...", "chain": "ethereum", "name": "Binance 14", "tier": "free"},
            ...
        ],
        "chains": ["ethereum", "bsc"],
    }
    """
    filters = [SmartLabel.address == address.lower()]
    if chain:
        filters.append(SmartLabel.chain == chain.lower())

    label = await db.scalar(select(SmartLabel).where(and_(*filters)).limit(1))
    if label is None:
        return None

    entity_name = normalize_entity_name(label.name)
    all_addrs = await get_entity_addresses(db, entity_name)

    chains = sorted({a.chain for a in all_addrs})
    return {
        "entity_name": entity_name,
        "entity_type": label.entity_type,
        "addresses": [
            {
                "address": a.address,
                "chain": a.chain,
                "name": a.name,
                "tier": a.tier,
            }
            for a in all_addrs
        ],
        "chains": chains,
    }


# ── Cross-chain activity ────────────────────────────────────────────────────

async def get_entity_activity(
    db: AsyncSession, entity_name: str, hours: int = 24
) -> dict:
    """
    Aggregate whale activity across ALL chains for a single entity.

    Returns
    -------
    {
        "entity_name": str,
        "entity_type": str,
        "addresses": [...],           — all known addresses
        "chains_active_today": [...],  — chains with alerts in the window
        "total_volume_usd": float,
        "buy_volume_usd": float,
        "sell_volume_usd": float,
        "alert_count": int,
        "recent_alerts": [...],        — last 10 alerts across all chains
        "per_chain": {
            "ethereum": {"volume_usd": ..., "alert_count": ..., "buy_count": ..., "sell_count": ...},
            ...
        },
        "pnl": {
            "total_realized_usd": float,
            "total_bought_usd": float,
            "total_sold_usd": float,
            "positions": int,
        },
    }
    """
    all_addrs = await get_entity_addresses(db, entity_name)
    if not all_addrs:
        return {
            "entity_name": entity_name,
            "entity_type": "unknown",
            "addresses": [],
            "chains_active_today": [],
            "total_volume_usd": 0.0,
            "buy_volume_usd": 0.0,
            "sell_volume_usd": 0.0,
            "alert_count": 0,
            "recent_alerts": [],
            "per_chain": {},
            "pnl": {
                "total_realized_usd": 0.0,
                "total_bought_usd": 0.0,
                "total_sold_usd": 0.0,
                "positions": 0,
            },
        }

    entity_type = all_addrs[0].entity_type
    addr_set = {a.address.lower() for a in all_addrs}
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)

    # ── Whale alerts in window ───────────────────────────────────────────
    alert_filters = [
        WhaleAlert.detected_at >= since,
        (WhaleAlert.from_address.in_(addr_set) | WhaleAlert.to_address.in_(addr_set)),
    ]

    # Per-chain aggregation
    chain_agg = await db.execute(
        select(
            WhaleAlert.chain,
            func.count(WhaleAlert.id).label("cnt"),
            func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("vol"),
            func.sum(
                case((WhaleAlert.direction == "BUY", WhaleAlert.amount_usd), else_=0.0)
            ).label("buy_vol"),
            func.sum(
                case((WhaleAlert.direction == "SELL", WhaleAlert.amount_usd), else_=0.0)
            ).label("sell_vol"),
            func.sum(
                case((WhaleAlert.direction == "BUY", 1), else_=0)
            ).label("buy_cnt"),
            func.sum(
                case((WhaleAlert.direction == "SELL", 1), else_=0)
            ).label("sell_cnt"),
        )
        .where(and_(*alert_filters))
        .group_by(WhaleAlert.chain)
    )

    per_chain: dict = {}
    total_vol = 0.0
    buy_vol = 0.0
    sell_vol = 0.0
    alert_count = 0

    for row in chain_agg.all():
        cv = float(row.vol or 0)
        bv = float(row.buy_vol or 0)
        sv = float(row.sell_vol or 0)
        per_chain[row.chain] = {
            "volume_usd": round(cv, 2),
            "alert_count": int(row.cnt),
            "buy_count": int(row.buy_cnt or 0),
            "sell_count": int(row.sell_cnt or 0),
            "buy_volume_usd": round(bv, 2),
            "sell_volume_usd": round(sv, 2),
        }
        total_vol += cv
        buy_vol += bv
        sell_vol += sv
        alert_count += int(row.cnt)

    chains_active = sorted(per_chain.keys())

    # ── Recent alerts (last 10) ──────────────────────────────────────────
    recent_result = await db.execute(
        select(WhaleAlert)
        .where(and_(*alert_filters))
        .order_by(desc(WhaleAlert.detected_at))
        .limit(10)
    )
    recent_alerts = [
        {
            "id": a.id,
            "chain": a.chain,
            "direction": a.direction,
            "token_symbol": a.token_symbol,
            "amount_usd": a.amount_usd,
            "from_address": a.from_address,
            "to_address": a.to_address,
            "tx_hash": a.tx_hash,
            "detected_at": a.detected_at.isoformat() if a.detected_at else None,
        }
        for a in recent_result.scalars().all()
    ]

    # ── P&L across all chains ────────────────────────────────────────────
    pnl_result = await db.execute(
        select(
            func.sum(WalletPosition.realized_pnl_usd).label("pnl"),
            func.sum(WalletPosition.total_bought_usd).label("bought"),
            func.sum(WalletPosition.total_sold_usd).label("sold"),
            func.count(WalletPosition.id).label("positions"),
        )
        .where(WalletPosition.wallet_address.in_(addr_set))
    )
    pnl_row = pnl_result.one()

    return {
        "entity_name": entity_name,
        "entity_type": entity_type,
        "addresses": [
            {
                "address": a.address,
                "chain": a.chain,
                "name": a.name,
                "tier": a.tier,
            }
            for a in all_addrs
        ],
        "chains_active_today": chains_active,
        "total_volume_usd": round(total_vol, 2),
        "buy_volume_usd": round(buy_vol, 2),
        "sell_volume_usd": round(sell_vol, 2),
        "alert_count": alert_count,
        "recent_alerts": recent_alerts,
        "per_chain": per_chain,
        "pnl": {
            "total_realized_usd": round(float(pnl_row.pnl or 0), 2),
            "total_bought_usd": round(float(pnl_row.bought or 0), 2),
            "total_sold_usd": round(float(pnl_row.sold or 0), 2),
            "positions": int(pnl_row.positions or 0),
        },
    }


# ── Alert enrichment (used by whale_tracker) ────────────────────────────────

async def enrich_alert_cross_chain(
    db: AsyncSession, wallet_address: str, chain: str
) -> Optional[dict]:
    """
    Look up the entity behind `wallet_address` and check if it was also
    active on other chains in the last 24 hours.

    Returns None if the address is not a known entity.

    Response shape:
    {
        "entity_name": "Jump Trading",
        "entity_type": "vc",
        "total_addresses": 5,
        "also_active_on": ["bsc", "arbitrum"],  — other chains with alerts today
        "cross_chain_volume_usd": 12345678.90,
        "cross_chain_alert_count": 42,
    }
    """
    label = await db.scalar(
        select(SmartLabel).where(
            and_(SmartLabel.address == wallet_address.lower(), SmartLabel.chain == chain.lower())
        )
    )
    if label is None:
        return None

    entity_name = normalize_entity_name(label.name)
    all_addrs = await get_entity_addresses(db, entity_name)
    addr_set = {a.address.lower() for a in all_addrs}
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

    # Which chains had alerts today?
    chain_result = await db.execute(
        select(
            WhaleAlert.chain,
            func.count(WhaleAlert.id).label("cnt"),
            func.coalesce(func.sum(WhaleAlert.amount_usd), 0.0).label("vol"),
        )
        .where(and_(
            WhaleAlert.detected_at >= since,
            (WhaleAlert.from_address.in_(addr_set) | WhaleAlert.to_address.in_(addr_set)),
        ))
        .group_by(WhaleAlert.chain)
    )

    total_vol = 0.0
    total_alerts = 0
    active_chains: list[str] = []

    for row in chain_result.all():
        active_chains.append(row.chain)
        total_vol += float(row.vol or 0)
        total_alerts += int(row.cnt)

    # "Also active on" = other chains, excluding current
    also_active = [c for c in sorted(active_chains) if c != chain.lower()]

    return {
        "entity_name": entity_name,
        "entity_type": label.entity_type,
        "total_addresses": len(all_addrs),
        "also_active_on": also_active,
        "cross_chain_volume_usd": round(total_vol, 2),
        "cross_chain_alert_count": total_alerts,
    }


# ── Entity listing ───────────────────────────────────────────────────────────

async def list_entities(db: AsyncSession) -> list[dict]:
    """
    Return all unique entities derived from the SmartLabel table,
    grouped by normalised name, with address count and chains.
    """
    result = await db.execute(select(SmartLabel))
    all_labels = list(result.scalars().all())

    entity_map: dict[str, dict] = {}
    for lbl in all_labels:
        key = normalize_entity_name(lbl.name)
        if key not in entity_map:
            entity_map[key] = {
                "entity_name": key,
                "entity_type": lbl.entity_type,
                "address_count": 0,
                "chains": set(),
            }
        entity_map[key]["address_count"] += 1
        entity_map[key]["chains"].add(lbl.chain)

    return [
        {
            "entity_name": v["entity_name"],
            "entity_type": v["entity_type"],
            "address_count": v["address_count"],
            "chains": sorted(v["chains"]),
        }
        for v in sorted(entity_map.values(), key=lambda x: x["address_count"], reverse=True)
    ]
