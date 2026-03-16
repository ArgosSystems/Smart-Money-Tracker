"""
api/routers/alert_channels.py
-------------------------------
CRUD for Discord alert channel configuration.

Routes
------
POST   /api/v1/alert-channels             — register a channel for auto-push
GET    /api/v1/alert-channels             — list configured channels
GET    /api/v1/alert-channels/active      — list only active channels (used by bot)
DELETE /api/v1/alert-channels/{id}        — remove a channel
PATCH  /api/v1/alert-channels/{id}/toggle — enable / disable
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AlertChannel, GuildSubscription, get_db

router = APIRouter(prefix="/api/v1/alert-channels", tags=["Alert Channels"])


# ── Request / response schemas ────────────────────────────────────────────────

class AlertChannelCreate(BaseModel):
    guild_id: str
    channel_id: str
    min_score: float = 0.0
    chains: Optional[str] = None        # comma-separated, e.g. "ethereum,bsc"
    alert_types: Optional[str] = None   # comma-separated, e.g. "whale,price"


class AlertChannelResponse(BaseModel):
    id: int
    guild_id: str
    channel_id: str
    min_score: float
    chains: Optional[str]
    alert_types: Optional[str]
    is_active: bool
    guild_tier: str = "free"

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=AlertChannelResponse, status_code=201)
async def create_alert_channel(
    body: AlertChannelCreate,
    db: AsyncSession = Depends(get_db),
) -> AlertChannel:
    """Register a Discord channel to receive real-time alert push notifications."""
    # Check for duplicate
    existing = await db.execute(
        select(AlertChannel).where(
            AlertChannel.guild_id == body.guild_id,
            AlertChannel.channel_id == body.channel_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Channel already configured")

    channel = AlertChannel(
        guild_id=body.guild_id,
        channel_id=body.channel_id,
        min_score=body.min_score,
        chains=body.chains,
        alert_types=body.alert_types,
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


@router.get("", response_model=list[AlertChannelResponse])
async def list_alert_channels(
    guild_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[AlertChannel]:
    """List all configured alert channels, optionally filtered by guild."""
    query = select(AlertChannel)
    if guild_id:
        query = query.where(AlertChannel.guild_id == guild_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/active", response_model=list[AlertChannelResponse])
async def list_active_channels(
    db: AsyncSession = Depends(get_db),
):
    """List only active channels — used by the Discord bot's push task. Includes guild_tier."""
    stmt = (
        select(AlertChannel, GuildSubscription.tier)
        .outerjoin(GuildSubscription, AlertChannel.guild_id == GuildSubscription.guild_id)
        .where(AlertChannel.is_active.is_(True))
    )
    result = await db.execute(stmt)
    
    response = []
    for channel, tier in result.all():
        data = {
            "id": channel.id,
            "guild_id": channel.guild_id,
            "channel_id": channel.channel_id,
            "min_score": channel.min_score,
            "chains": channel.chains,
            "alert_types": channel.alert_types,
            "is_active": channel.is_active,
            "guild_tier": tier or "free"
        }
        response.append(data)
    return response


@router.delete("/{channel_db_id}", status_code=204, response_class=Response)
async def delete_alert_channel(
    channel_db_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove an alert channel configuration."""
    channel = await db.get(AlertChannel, channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Alert channel not found")
    await db.delete(channel)
    await db.commit()
    return Response(status_code=204)


@router.patch("/{channel_db_id}/toggle", response_model=AlertChannelResponse)
async def toggle_alert_channel(
    channel_db_id: int,
    db: AsyncSession = Depends(get_db),
) -> AlertChannel:
    """Enable or disable an alert channel."""
    channel = await db.get(AlertChannel, channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Alert channel not found")
    channel.is_active = not channel.is_active
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel
