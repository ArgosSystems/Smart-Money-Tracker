"""
api/routers/cross_chain.py
----------------------------
Cross-chain entity detection endpoints.

Routes
------
GET  /api/v1/entity/list                 – all known entities with address counts
GET  /api/v1/entity/lookup/{address}     – resolve an address to its entity + all chain addresses
GET  /api/v1/entity/{name}               – full cross-chain profile for a named entity
GET  /api/v1/entity/{name}/activity      – recent cross-chain whale activity for an entity
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import get_db
from api.services.cross_chain_entity import (
    get_entity_activity,
    list_entities,
    resolve_entity_from_address,
)

router = APIRouter(prefix="/api/v1/entity", tags=["Cross-Chain Entity"])


@router.get(
    "/list",
    summary="List all known entities from the SmartLabel database",
)
async def entity_list(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    Return all unique entities derived from the SmartLabel table,
    grouped by normalised name, with address count and chains.
    """
    return await list_entities(db)


@router.get(
    "/lookup/{address}",
    summary="Resolve an address to its cross-chain entity",
)
async def entity_lookup(
    address: str,
    chain: Optional[str] = Query(default=None, description="Hint chain (checks all if omitted)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Given any wallet address, find its SmartLabel entity and return
    all known addresses across all chains for the same entity.
    """
    result = await resolve_entity_from_address(db, address, chain)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Address {address} not found in the SmartLabel database.",
        )
    return result


@router.get(
    "/{name}",
    summary="Full cross-chain profile for a named entity",
)
async def entity_profile(
    name: str,
    hours: int = Query(default=24, ge=1, le=168, description="Activity look-back window in hours"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return the full cross-chain profile for an entity:
    all known addresses, per-chain volume breakdown, combined P&L,
    and the last 10 alerts across all chains.

    `name` is case-insensitive and normalised (e.g. "jump trading" matches
    "Jump Trading", "Jump Trading 2", etc.).
    """
    data = await get_entity_activity(db, name, hours=hours)
    if not data["addresses"]:
        raise HTTPException(
            status_code=404,
            detail=f"Entity '{name}' not found. Use /api/v1/entity/list to see all entities.",
        )
    return data


@router.get(
    "/{name}/activity",
    summary="Recent cross-chain whale activity for an entity",
)
async def entity_activity(
    name: str,
    hours: int = Query(default=24, ge=1, le=168, description="Activity look-back window in hours"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Same as the entity profile but focused on activity:
    per-chain breakdown, recent alerts, and combined volume.
    """
    data = await get_entity_activity(db, name, hours=hours)
    if not data["addresses"]:
        raise HTTPException(
            status_code=404,
            detail=f"Entity '{name}' not found.",
        )
    return {
        "entity_name": data["entity_name"],
        "entity_type": data["entity_type"],
        "hours": hours,
        "chains_active": data["chains_active_today"],
        "total_volume_usd": data["total_volume_usd"],
        "buy_volume_usd": data["buy_volume_usd"],
        "sell_volume_usd": data["sell_volume_usd"],
        "alert_count": data["alert_count"],
        "per_chain": data["per_chain"],
        "recent_alerts": data["recent_alerts"],
    }
