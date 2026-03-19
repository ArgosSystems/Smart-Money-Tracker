"""
api/routers/clusters.py
------------------------
Wallet cluster read endpoints.

Routes
------
GET  /api/v1/clusters                    – list all detected clusters (paginated)
GET  /api/v1/clusters/wallet/{address}   – clusters containing a specific wallet
GET  /api/v1/clusters/{id}               – get a single cluster with its members
POST /api/v1/clusters/analyze            – trigger an immediate re-analysis cycle
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_serializer
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import ClusterMembership, WalletCluster, get_db
from api.services.cluster_detector import _run_analysis

router = APIRouter(prefix="/api/v1", tags=["Wallet Clusters"])


# ── Response schemas ──────────────────────────────────────────────────────────

class ClusterMemberResponse(BaseModel):
    id: int
    wallet_address: str
    chain: str
    wallet_label: Optional[str]
    detection_signals: str
    confidence: float
    added_at: datetime.datetime

    model_config = {"from_attributes": True}

    @field_serializer("added_at")
    def serialize_added_at(self, v: datetime.datetime) -> str:
        return v.isoformat() if v else ""


class WalletClusterResponse(BaseModel):
    id: int
    chain: str
    cluster_label: Optional[str]
    detection_methods: str
    confidence: float
    member_count: int
    total_volume_usd: float
    first_detected_at: datetime.datetime
    last_updated_at: datetime.datetime
    members: list[ClusterMemberResponse] = []

    model_config = {"from_attributes": True}

    @field_serializer("first_detected_at", "last_updated_at")
    def serialize_dt(self, v: datetime.datetime) -> str:
        return v.isoformat() if v else ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/clusters",
    response_model=list[WalletClusterResponse],
    summary="List detected wallet clusters",
)
async def list_clusters(
    chain: Optional[str] = Query(default=None, description="Filter by chain name"),
    min_confidence: float = Query(default=0.0, description="Minimum confidence score (0.0-1.0)"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
) -> list[WalletClusterResponse]:
    """
    Return detected wallet clusters, newest first.

    Clusters are re-detected every 10 minutes from the last 7 days of whale alerts.
    Use min_confidence to filter out weak signals (0.70+ recommended).
    """
    query = select(WalletCluster).order_by(desc(WalletCluster.first_detected_at))

    filters = []
    if chain:
        filters.append(WalletCluster.chain == chain.lower())
    if min_confidence > 0.0:
        filters.append(WalletCluster.confidence >= min_confidence)
    if filters:
        query = query.where(and_(*filters))

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    clusters = result.scalars().all()

    # Eagerly load members for each cluster
    out = []
    for cluster in clusters:
        members_result = await db.execute(
            select(ClusterMembership)
            .where(ClusterMembership.cluster_id == cluster.id)
            .order_by(ClusterMembership.added_at)
        )
        members = list(members_result.scalars().all())
        cluster_dict = {
            "id":                 cluster.id,
            "chain":              cluster.chain,
            "cluster_label":      cluster.cluster_label,
            "detection_methods":  cluster.detection_methods,
            "confidence":         cluster.confidence,
            "member_count":       cluster.member_count,
            "total_volume_usd":   cluster.total_volume_usd,
            "first_detected_at":  cluster.first_detected_at,
            "last_updated_at":    cluster.last_updated_at,
            "members": [
                {
                    "id":                m.id,
                    "wallet_address":    m.wallet_address,
                    "chain":             m.chain,
                    "wallet_label":      m.wallet_label,
                    "detection_signals": m.detection_signals,
                    "confidence":        m.confidence,
                    "added_at":          m.added_at,
                }
                for m in members
            ],
        }
        out.append(WalletClusterResponse.model_validate(cluster_dict))
    return out


@router.get(
    "/clusters/wallet/{wallet_address}",
    response_model=list[WalletClusterResponse],
    summary="Get clusters containing a specific wallet address",
)
async def get_clusters_for_wallet(
    wallet_address: str,
    chain: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[WalletClusterResponse]:
    """
    Return all clusters that contain the given wallet address.

    Useful for: 'which entity does 0x1234… belong to?'
    """
    filters = [ClusterMembership.wallet_address == wallet_address.lower()]
    if chain:
        filters.append(ClusterMembership.chain == chain.lower())

    memberships_result = await db.execute(
        select(ClusterMembership).where(and_(*filters))
    )
    memberships = list(memberships_result.scalars().all())
    if not memberships:
        return []

    out = []
    seen_cluster_ids: set[int] = set()
    for membership in memberships:
        if membership.cluster_id in seen_cluster_ids:
            continue
        seen_cluster_ids.add(membership.cluster_id)

        cluster = await db.scalar(
            select(WalletCluster).where(WalletCluster.id == membership.cluster_id)
        )
        if cluster is None:
            continue

        members_result = await db.execute(
            select(ClusterMembership)
            .where(ClusterMembership.cluster_id == cluster.id)
            .order_by(ClusterMembership.added_at)
        )
        members = list(members_result.scalars().all())
        cluster_dict = {
            "id":                cluster.id,
            "chain":             cluster.chain,
            "cluster_label":     cluster.cluster_label,
            "detection_methods": cluster.detection_methods,
            "confidence":        cluster.confidence,
            "member_count":      cluster.member_count,
            "total_volume_usd":  cluster.total_volume_usd,
            "first_detected_at": cluster.first_detected_at,
            "last_updated_at":   cluster.last_updated_at,
            "members": [
                {
                    "id":                m.id,
                    "wallet_address":    m.wallet_address,
                    "chain":             m.chain,
                    "wallet_label":      m.wallet_label,
                    "detection_signals": m.detection_signals,
                    "confidence":        m.confidence,
                    "added_at":          m.added_at,
                }
                for m in members
            ],
        }
        out.append(WalletClusterResponse.model_validate(cluster_dict))
    return out


@router.get(
    "/clusters/{cluster_id}",
    response_model=WalletClusterResponse,
    summary="Get a single cluster by ID",
)
async def get_cluster(
    cluster_id: int,
    db: AsyncSession = Depends(get_db),
) -> WalletClusterResponse:
    """Return a single cluster with all of its members."""
    cluster = await db.scalar(
        select(WalletCluster).where(WalletCluster.id == cluster_id)
    )
    if cluster is None:
        raise HTTPException(status_code=404, detail=f"Cluster #{cluster_id} not found.")

    members_result = await db.execute(
        select(ClusterMembership)
        .where(ClusterMembership.cluster_id == cluster.id)
        .order_by(ClusterMembership.added_at)
    )
    members = list(members_result.scalars().all())

    cluster_dict = {
        "id":                cluster.id,
        "chain":             cluster.chain,
        "cluster_label":     cluster.cluster_label,
        "detection_methods": cluster.detection_methods,
        "confidence":        cluster.confidence,
        "member_count":      cluster.member_count,
        "total_volume_usd":  cluster.total_volume_usd,
        "first_detected_at": cluster.first_detected_at,
        "last_updated_at":   cluster.last_updated_at,
        "members": [
            {
                "id":                m.id,
                "wallet_address":    m.wallet_address,
                "chain":             m.chain,
                "wallet_label":      m.wallet_label,
                "detection_signals": m.detection_signals,
                "confidence":        m.confidence,
                "added_at":          m.added_at,
            }
            for m in members
        ],
    }
    return WalletClusterResponse.model_validate(cluster_dict)


@router.post(
    "/clusters/analyze",
    summary="Trigger immediate cluster re-analysis",
)
async def trigger_analysis() -> dict:
    """
    Trigger an immediate cluster re-analysis cycle (runs in background).
    The normal cycle runs every 10 minutes automatically.
    """
    asyncio.create_task(_run_analysis(set()), name="cluster_analysis_manual")
    return {"status": "analysis triggered", "message": "Cluster analysis running in background."}
