"""
api/routers/token_safety.py
----------------------------
Solana token safety check via RugCheck.xyz.

Routes
------
GET /api/v1/token-safety/{mint}  – full rug-check report for a Solana mint address
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Token Safety"])

_RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"


# ── Schemas ────────────────────────────────────────────────────────────────────

class RiskFactor(BaseModel):
    name: str
    description: str
    score: int
    level: str  # "danger" | "warn" | "info"


class TokenSafetyReport(BaseModel):
    mint: str
    name: Optional[str]
    symbol: Optional[str]
    score: int
    risk_level: str              # "SAFE" | "CAUTION" | "DANGER"
    rugged: bool
    mint_authority_revoked: bool
    freeze_authority_revoked: bool
    total_liquidity_usd: float
    lp_locked_pct: float
    top_holder_pct: float
    top5_holders_pct: float
    risks: list[RiskFactor]


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.get(
    "/token-safety/{mint}",
    response_model=TokenSafetyReport,
    summary="Solana token safety report (anti-rug check)",
)
async def token_safety(mint: str) -> TokenSafetyReport:
    """
    Fetch a rug-check safety report for a Solana token mint address.

    Checks:
    - Mint and freeze authority status (revoked = safe)
    - LP lock percentage across all markets
    - Top holder concentration (top 1 and top 5)
    - RugCheck risk score and individual risk factors

    Risk levels:
    - SAFE    → score < 500
    - CAUTION → score 500–1499
    - DANGER  → score ≥ 1500
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_RUGCHECK_URL.format(mint=mint))

            if resp.status_code != 200:
                # Capture the RugCheck error body for logging and user feedback
                try:
                    body = resp.json()
                    rc_msg = body.get("message") or body.get("error") or str(body)
                except Exception:
                    rc_msg = resp.text[:300]

                logger.warning(
                    "RugCheck %s → HTTP %d: %s", mint, resp.status_code, rc_msg
                )

                if resp.status_code in (400, 404, 422):
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Token not found on RugCheck (HTTP {resp.status_code}). "
                            f"Make sure the mint address is correct and the token exists on Solana. "
                            f"RugCheck said: {rc_msg}"
                        ),
                    )
                raise HTTPException(
                    status_code=502,
                    detail=f"RugCheck API returned HTTP {resp.status_code}: {rc_msg}",
                )

            data = resp.json()

    except HTTPException:
        raise
    except httpx.RequestError as exc:
        logger.error("RugCheck request error for %s: %s", mint, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach RugCheck API: {exc}",
        )

    score: int = int(data.get("score") or 0)
    if score < 500:
        risk_level = "SAFE"
    elif score < 1500:
        risk_level = "CAUTION"
    else:
        risk_level = "DANGER"

    token = data.get("token") or {}
    meta  = data.get("tokenMeta") or {}

    # LP locked % — take the maximum across all markets
    lp_locked_pct = 0.0
    for market in data.get("markets") or []:
        lp = market.get("lp") or {}
        lp_locked_pct = max(lp_locked_pct, float(lp.get("lpLocked") or 0))

    # Top holder concentration
    top_holders = data.get("topHolders") or []
    top_holder_pct  = float(top_holders[0].get("pct", 0)) if top_holders else 0.0
    top5_holders_pct = sum(float(h.get("pct", 0)) for h in top_holders[:5])

    risks = [
        RiskFactor(
            name=r.get("name") or "Unknown",
            description=r.get("description") or "",
            score=int(r.get("score") or 0),
            level=r.get("level") or "info",
        )
        for r in (data.get("risks") or [])
    ]

    return TokenSafetyReport(
        mint=data.get("mint") or mint,
        name=meta.get("name"),
        symbol=meta.get("symbol"),
        score=score,
        risk_level=risk_level,
        rugged=bool(data.get("rugged")),
        mint_authority_revoked=token.get("mintAuthority") is None,
        freeze_authority_revoked=token.get("freezeAuthority") is None,
        total_liquidity_usd=float(data.get("totalMarketLiquidity") or 0),
        lp_locked_pct=lp_locked_pct,
        top_holder_pct=top_holder_pct,
        top5_holders_pct=top5_holders_pct,
        risks=risks,
    )
