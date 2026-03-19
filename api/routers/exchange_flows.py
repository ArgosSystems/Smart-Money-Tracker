"""
api/routers/exchange_flows.py
-------------------------------
Exchange flow event read endpoints.

Routes
------
GET  /api/v1/exchange-flows            – recent exchange flow events (paginated)
GET  /api/v1/exchange-flows/wallet/{addr} – flows for a specific whale wallet
"""

from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_serializer
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import ExchangeFlowEvent, get_db

router = APIRouter(prefix="/api/v1", tags=["Exchange Flows"])


# ── Response schema ───────────────────────────────────────────────────────────

class ExchangeFlowResponse(BaseModel):
    id: int
    whale_alert_id: int
    wallet_address: str
    chain: str
    exchange_name: str
    exchange_address: str
    flow_direction: str         # OUTFLOW | INFLOW
    token_symbol: Optional[str]
    token_address: Optional[str]
    amount_usd: float
    amount_token: float
    tx_hash: str
    fired_at: datetime.datetime

    model_config = {"from_attributes": True}

    @field_serializer("fired_at")
    def serialize_fired_at(self, v: datetime.datetime) -> str:
        return v.isoformat() if v else ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/exchange-flows",
    response_model=list[ExchangeFlowResponse],
    summary="Get recent exchange flow events",
)
async def get_exchange_flows(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    chain: Optional[str] = Query(default=None, description="Filter by chain name"),
    direction: Optional[str] = Query(
        default=None, description="OUTFLOW (sell) or INFLOW (buy)"
    ),
    exchange: Optional[str] = Query(
        default=None, description="Filter by exchange name (partial match)"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[ExchangeFlowResponse]:
    """
    Return recent exchange flow events, newest first.

    OUTFLOW = whale sent tokens TO an exchange (sell signal 🔴).
    INFLOW  = whale received tokens FROM an exchange (buy signal 🟢).
    """
    query = select(ExchangeFlowEvent).order_by(desc(ExchangeFlowEvent.fired_at))

    filters = []
    if chain:
        filters.append(ExchangeFlowEvent.chain == chain.lower())
    if direction:
        filters.append(ExchangeFlowEvent.flow_direction == direction.upper())
    if exchange:
        filters.append(ExchangeFlowEvent.exchange_name.ilike(f"%{exchange}%"))
    if filters:
        query = query.where(and_(*filters))

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return [ExchangeFlowResponse.model_validate(row) for row in result.scalars().all()]


@router.get(
    "/exchange-flows/wallet/{wallet_address}",
    response_model=list[ExchangeFlowResponse],
    summary="Get exchange flow events for a specific wallet",
)
async def get_exchange_flows_for_wallet(
    wallet_address: str,
    limit: int = Query(default=20, le=100),
    chain: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[ExchangeFlowResponse]:
    """Return exchange flow events involving a specific whale wallet address."""
    filters = [ExchangeFlowEvent.wallet_address == wallet_address.lower()]
    if chain:
        filters.append(ExchangeFlowEvent.chain == chain.lower())

    result = await db.execute(
        select(ExchangeFlowEvent)
        .where(and_(*filters))
        .order_by(desc(ExchangeFlowEvent.fired_at))
        .limit(limit)
    )
    return [ExchangeFlowResponse.model_validate(row) for row in result.scalars().all()]
