"""
api/routers/bluesky.py
-----------------------
REST endpoints for Bluesky broadcaster management.

Routes
------
GET  /api/v1/bluesky/status   — broadcaster status (queue, budget, circuit breaker)
GET  /api/v1/bluesky/recent   — last N posted/dry-run posts from DB
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import BlueSkyPost, get_db
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bluesky", tags=["Bluesky"])


@router.get("/status", summary="Bluesky broadcaster status")
async def bluesky_status() -> dict:
    """
    Return the current state of the Bluesky broadcaster.
    Returns 503 if not enabled.
    """
    if not settings.bluesky.enabled:
        raise HTTPException(status_code=503, detail="Bluesky broadcasting is not enabled")

    from api.events.dispatcher import event_dispatcher  # noqa: PLC0415

    plugin = event_dispatcher._plugins.get("bluesky")
    if plugin is None:
        raise HTTPException(status_code=503, detail="Bluesky plugin not registered")

    return plugin.status  # type: ignore[attr-defined]


@router.get("/recent", summary="Recent Bluesky posts")
async def recent_bluesky_posts(
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return the most recent Bluesky posts from the database."""
    result = await db.execute(
        select(BlueSkyPost)
        .order_by(desc(BlueSkyPost.posted_at))
        .limit(limit)
    )
    posts = result.scalars().all()
    return [
        {
            "id": p.id,
            "alert_type": p.alert_type,
            "alert_id": p.alert_id,
            "post_uri": p.post_uri,
            "post_cid": p.post_cid,
            "content": p.content,
            "priority_score": p.priority_score,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
        }
        for p in posts
    ]
