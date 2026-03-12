"""
api/routers/price_alerts.py
----------------------------
CRUD endpoints for price alert rules.

Routes
------
POST   /api/v1/price-alerts             – create a new rule
GET    /api/v1/price-alerts             – list all rules (filter by chain / active)
GET    /api/v1/price-alerts/{id}        – get single rule
DELETE /api/v1/price-alerts/{id}        – delete a rule
PATCH  /api/v1/price-alerts/{id}/toggle – enable / disable a rule
"""

from __future__ import annotations

import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import PriceAlertRule, get_db
from config.chains import CHAINS

router = APIRouter(prefix="/api/v1/price-alerts", tags=["Price Alerts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PriceAlertCreate(BaseModel):
    chain: str = Field("ethereum", description="Chain name (ethereum, bsc, polygon, …)")
    token_address: str = Field(..., description="ERC-20 contract address (0x…)")
    token_symbol: str = Field(..., description="Token symbol, e.g. PEPE")
    condition: Literal["above", "below"] = Field(..., description="Trigger when price is above or below target")
    target_price_usd: float = Field(..., gt=0, description="USD price threshold")
    label: Optional[str] = Field(None, max_length=100, description="Optional human-readable label")


class PriceAlertResponse(BaseModel):
    id: int
    chain: str
    token_address: str
    token_symbol: str
    condition: str
    target_price_usd: float
    is_active: bool
    label: Optional[str]
    created_at: datetime.datetime
    last_triggered_at: Optional[datetime.datetime]

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_triggered_at")
    def serialize_dt(self, v: Optional[datetime.datetime]) -> Optional[str]:
        return v.isoformat() if v else None


class PriceAlertToggleResponse(BaseModel):
    id: int
    is_active: bool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_chain(chain: str) -> str:
    chain = chain.lower()
    if chain not in CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown chain '{chain}'. Supported: {list(CHAINS.keys())}",
        )
    return chain


async def _get_rule_or_404(rule_id: int, db: AsyncSession) -> PriceAlertRule:
    rule = await db.get(PriceAlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Price alert rule {rule_id} not found")
    return rule


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=PriceAlertResponse, status_code=201, summary="Create price alert rule")
async def create_price_alert(
    body: PriceAlertCreate,
    db: AsyncSession = Depends(get_db),
) -> PriceAlertResponse:
    """
    Create a new price alert rule.

    When the token price on the specified chain crosses `target_price_usd`
    in the direction defined by `condition`, an event is broadcast to all
    WebSocket subscribers at `ws://localhost:8000/ws/alerts`.
    """
    chain = _validate_chain(body.chain)
    rule = PriceAlertRule(
        chain=chain,
        token_address=body.token_address.lower(),
        token_symbol=body.token_symbol.upper(),
        condition=body.condition,
        target_price_usd=body.target_price_usd,
        label=body.label,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return PriceAlertResponse.model_validate(rule)


@router.get("", response_model=list[PriceAlertResponse], summary="List price alert rules")
async def list_price_alerts(
    chain: Optional[str] = Query(default=None, description="Filter by chain"),
    active_only: bool = Query(default=False, description="Return only active rules"),
    db: AsyncSession = Depends(get_db),
) -> list[PriceAlertResponse]:
    """Return all price alert rules, optionally filtered."""
    query = select(PriceAlertRule).order_by(PriceAlertRule.created_at.desc())
    if chain:
        query = query.where(PriceAlertRule.chain == chain.lower())
    if active_only:
        query = query.where(PriceAlertRule.is_active == True)  # noqa: E712
    result = await db.execute(query)
    return [PriceAlertResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/{rule_id}", response_model=PriceAlertResponse, summary="Get a price alert rule")
async def get_price_alert(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> PriceAlertResponse:
    rule = await _get_rule_or_404(rule_id, db)
    return PriceAlertResponse.model_validate(rule)


@router.delete("/{rule_id}", status_code=204, response_model=None, summary="Delete a price alert rule")
async def delete_price_alert(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    rule = await _get_rule_or_404(rule_id, db)
    await db.delete(rule)
    await db.commit()
    return Response(status_code=204)


@router.patch("/{rule_id}/toggle", response_model=PriceAlertToggleResponse, summary="Toggle a price alert rule on/off")
async def toggle_price_alert(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> PriceAlertToggleResponse:
    """Flip is_active between True and False."""
    rule = await _get_rule_or_404(rule_id, db)
    rule.is_active = not rule.is_active
    db.add(rule)
    await db.commit()
    return PriceAlertToggleResponse(id=rule.id, is_active=rule.is_active)
