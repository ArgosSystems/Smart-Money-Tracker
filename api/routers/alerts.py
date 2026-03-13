"""
api/routers/alerts.py
----------------------
Alert read endpoints + WebSocket real-time stream.

Routes
------
GET  /api/v1/alerts                  – recent whale alerts (paginated, chain-filterable)
GET  /api/v1/alerts/token/{token}    – alerts filtered by token symbol or address
GET  /ws/alerts                      – WebSocket stream of live whale alerts
"""

from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, field_serializer
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.models import TrackedWallet, WhaleAlert, get_db
from api.services.broadcaster import alert_broadcaster

router = APIRouter(prefix="/api/v1", tags=["Alerts"])
ws_router = APIRouter(tags=["Alerts"])


# ── Response schema ───────────────────────────────────────────────────────────

class AlertResponse(BaseModel):
    id: int
    chain: str
    tx_hash: str
    from_address: str
    to_address: str
    token_symbol: Optional[str]
    token_address: Optional[str]
    amount_token: float
    amount_usd: float
    direction: str
    block_number: int
    detected_at: datetime.datetime
    wallet_label: Optional[str] = None

    model_config = {"from_attributes": True}

    @field_serializer("detected_at")
    def serialize_detected_at(self, v: datetime.datetime) -> str:
        return v.isoformat() if v else ""


def _to_response(alert: WhaleAlert) -> AlertResponse:
    """Convert ORM row → response model, including the wallet label if available."""
    resp = AlertResponse.model_validate(alert)
    resp.wallet_label = alert.wallet.label if alert.wallet else None
    return resp


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/alerts",
    response_model=list[AlertResponse],
    summary="Get recent whale alerts",
)
async def get_alerts(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    chain: Optional[str] = Query(default=None, description="Filter by chain name"),
    direction: Optional[str] = Query(default=None, description="BUY | SELL | SEND"),
    db: AsyncSession = Depends(get_db),
) -> list[AlertResponse]:
    """Return recent whale alerts, newest first."""
    query = (
        select(WhaleAlert)
        .options(joinedload(WhaleAlert.wallet))
        .order_by(desc(WhaleAlert.detected_at))
        .offset(offset)
        .limit(limit)
    )

    if chain:
        query = query.where(WhaleAlert.chain == chain.lower())
    if direction:
        query = query.where(WhaleAlert.direction == direction.upper())

    result = await db.execute(query)
    return [_to_response(a) for a in result.scalars().all()]


@router.get(
    "/alerts/token/{token}",
    response_model=list[AlertResponse],
    summary="Whale activity for a specific token",
)
async def get_token_alerts(
    token: str,
    limit: int = Query(default=50, le=200),
    chain: Optional[str] = Query(default=None, description="Filter by chain name"),
    db: AsyncSession = Depends(get_db),
) -> list[AlertResponse]:
    """
    Return alerts for a specific token.
    `token` can be a symbol (e.g. 'PEPE') or a full contract address (0x…).
    """
    query = (
        select(WhaleAlert)
        .options(joinedload(WhaleAlert.wallet))
        .order_by(desc(WhaleAlert.detected_at))
        .limit(limit)
    )

    if token.startswith("0x") and len(token) == 42:
        query = query.where(WhaleAlert.token_address == token.lower())
    else:
        query = query.where(WhaleAlert.token_symbol == token.upper())

    if chain:
        query = query.where(WhaleAlert.chain == chain.lower())

    result = await db.execute(query)
    return [_to_response(a) for a in result.scalars().all()]


# ── WebSocket ─────────────────────────────────────────────────────────────────

@ws_router.websocket("/ws/alerts")
async def websocket_alerts(
    websocket: WebSocket,
    chain: Optional[str] = Query(default=None, description="Filter by chain (e.g. ethereum, bsc)"),
) -> None:
    """
    WebSocket endpoint — streams new whale alerts in real-time.

    Connect with:  ws://localhost:8000/ws/alerts
    Filter chain:  ws://localhost:8000/ws/alerts?chain=ethereum

    Each message is a JSON object matching the AlertResponse schema.
    The connection stays open indefinitely; the server sends data only when
    new alerts are detected.
    """
    await websocket.accept()
    queue = alert_broadcaster.subscribe()
    chain_filter = chain.lower() if chain else None

    try:
        while True:
            data: dict = await queue.get()

            # Apply optional chain filter server-side
            if chain_filter and data.get("chain") != chain_filter:
                continue

            await websocket.send_json(data)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        alert_broadcaster.unsubscribe(queue)
