"""
api/routers/twitter.py
-----------------------
REST endpoints for Twitter broadcaster management and observability.

Routes
------
GET  /api/v1/twitter/status   — broadcaster status (queue, budget, circuit breaker)
GET  /api/v1/twitter/recent   — last N posted/dry-run tweets from DB
GET  /api/v1/twitter/preview  — preview tweet rendering for a specific alert
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import TwitterPost, WhaleAlert, PriceAlertRule, get_db
from api.events.protocol import AlertType
from api.events.types import WhaleAlertEvent, PriceTriggerEvent
from api.services.twitter.scoring import AlertScorer
from api.services.twitter.templates import TweetRenderer
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/twitter", tags=["Twitter"])

# Shared instances for preview rendering
_scorer = AlertScorer(weights=settings.twitter.scoring_weights)
_renderer = TweetRenderer()


@router.get("/status", summary="Twitter broadcaster status")
async def twitter_status() -> dict:
    """
    Return the current state of the Twitter broadcaster.

    Includes mode, queue depth, rate limiter budget, circuit breaker state,
    and feature flags.  Returns 503 if Twitter is not enabled.
    """
    if not settings.twitter.enabled:
        raise HTTPException(status_code=503, detail="Twitter broadcasting is not enabled")

    # Import lazily to avoid circular imports
    from api.events.dispatcher import event_dispatcher  # noqa: PLC0415

    plugin = event_dispatcher._plugins.get("twitter")
    if plugin is None:
        raise HTTPException(status_code=503, detail="Twitter plugin not registered")

    return plugin.status  # type: ignore[attr-defined]


@router.get("/recent", summary="Recent tweets")
async def recent_tweets(
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return the most recent tweets from the database."""
    result = await db.execute(
        select(TwitterPost)
        .order_by(desc(TwitterPost.posted_at))
        .limit(limit)
    )
    posts = result.scalars().all()
    return [
        {
            "id": p.id,
            "alert_type": p.alert_type,
            "alert_id": p.alert_id,
            "tweet_id": p.tweet_id,
            "content": p.content,
            "priority_score": p.priority_score,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "engagement_metrics": p.engagement_metrics,
        }
        for p in posts
    ]


@router.get("/preview", summary="Preview tweet rendering")
async def preview_tweet(
    alert_id: int = Query(..., description="Alert ID"),
    alert_type: str = Query(default="whale", description="whale or price"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Render a tweet preview for a specific alert without posting.

    Returns the formatted content, priority score, and whether it would
    pass all gates (rate limit, cooldown, circuit breaker).
    """
    if alert_type == "whale":
        alert = await db.get(WhaleAlert, alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="Whale alert not found")

        event = WhaleAlertEvent(
            alert_id=alert.id,
            chain=alert.chain,
            timestamp=alert.detected_at or datetime.datetime.utcnow(),
            metadata={
                "tx_hash": alert.tx_hash,
                "from_address": alert.from_address,
                "to_address": alert.to_address,
                "from_label": None,
                "to_label": None,
                "token_symbol": alert.token_symbol,
                "token_address": alert.token_address,
                "amount_token": alert.amount_token,
                "amount_usd": alert.amount_usd,
                "direction": alert.direction,
                "block_number": alert.block_number,
                "detected_at": alert.detected_at.isoformat() if alert.detected_at else None,
                "smart_money_score": None,
                "entity_type": "unknown",
            },
        )
    elif alert_type == "price":
        rule = await db.get(PriceAlertRule, alert_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Price alert rule not found")

        event = PriceTriggerEvent(
            alert_id=rule.id,
            chain=rule.chain,
            timestamp=rule.last_triggered_at or datetime.datetime.utcnow(),
            metadata={
                "rule_id": rule.id,
                "token_symbol": rule.token_symbol,
                "token_address": rule.token_address,
                "condition": rule.condition,
                "target_price_usd": rule.target_price_usd,
                "current_price_usd": rule.target_price_usd,
                "label": rule.label,
                "pct_change_24h": None,
            },
        )
    else:
        raise HTTPException(status_code=400, detail="alert_type must be 'whale' or 'price'")

    score = _scorer.score(event)
    content = _renderer.render(event, score)

    # Determine if it would pass gates
    would_post = True
    skip_reason = ""

    if not settings.twitter.enabled:
        would_post = False
        skip_reason = "Twitter broadcasting is disabled"
    elif score <= 0:
        would_post = False
        skip_reason = "Score is zero (suppressed)"

    return {
        "content": content,
        "score": score,
        "char_count": len(content),
        "would_post": would_post,
        "skip_reason": skip_reason,
    }
