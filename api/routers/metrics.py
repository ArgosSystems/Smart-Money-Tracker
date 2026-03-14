"""
api/routers/metrics.py
-----------------------
Observability endpoints for the event dispatcher and broadcaster plugins.

Routes
------
GET  /api/v1/metrics/events  — dispatcher metrics (total dispatched, per-plugin stats)
"""

from __future__ import annotations

from fastapi import APIRouter

from api.events.dispatcher import event_dispatcher

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


@router.get("/events", summary="Event dispatcher metrics")
async def event_metrics() -> dict:
    """Return delivery metrics for all registered broadcaster plugins."""
    return event_dispatcher.dispatch_metrics
