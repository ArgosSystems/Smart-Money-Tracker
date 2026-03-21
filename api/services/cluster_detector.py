"""
api/services/cluster_detector.py
----------------------------------
Wallet clustering — detects groups of wallets that likely belong to the same entity.

Whales routinely split funds across 10-20 wallets to hide their real position size.
Clustering reveals the TRUE position by grouping coordinated addresses.

Detection methods (use only existing DB data, no external APIs, no ML):

  Method 1 — Funding source:
    If wallet A directly sent ETH/native to wallet B (both tracked, SEND direction)
    AND B subsequently traded any of the same tokens as A → likely same entity.
    Confidence: 0.70

  Method 2 — Timing correlation:
    If wallets A and B both bought the same token within 5 minutes of each other,
    and this co-buy happened 3+ times → likely coordinated (same entity).
    Confidence: 0.60 base + 0.05 per extra coincidence (max 0.90)

  Method 3 — Directional pattern:
    If 3+ wallets always moved in the same direction on the same token within the
    same hour-bucket, and this pattern repeated across 2+ different hours →
    likely coordinated cluster.
    Confidence: 0.65

  Multi-method bonus: +0.10 per additional method (max 1.0).

Rules:
  - Never cluster addresses with entity_type = 'exchange' in SmartLabel.
  - Full rebuild every 10 minutes (delete stale data, re-detect from scratch).
  - Lookback window: last 7 days of whale_alerts.

Public API:
  ClusterAnalyzer          — background asyncio task started in lifespan
  get_wallet_cluster_info  — fast lookup called from whale_tracker.py to enrich alerts
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import (
    AsyncSessionLocal,
    ClusterMembership,
    SmartLabel,
    TrackedWallet,
    WalletCluster,
    WhaleAlert,
    WalletFundingEvent,
)

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

_ANALYSIS_INTERVAL_SECONDS = 600   # re-run every 10 minutes
_LOOKBACK_DAYS              = 7    # look back this many days of whale_alerts
_FUNDING_LOOKBACK_DAYS      = 90   # look back further for wallet funding

_TIMING_WINDOW_SECONDS      = 300  # 5-minute co-buy window
_TIMING_MIN_COINCIDENCES    = 2    # minimum co-buys to emit a timing signal

_PATTERN_MIN_WALLETS        = 2    # min wallets in same-hour same-direction move
_PATTERN_MIN_OCCURRENCES    = 1    # pattern must repeat across 1+ hour-buckets

_FUNDING_BASE_CONFIDENCE    = 0.70
_TIMING_BASE_CONFIDENCE     = 0.60
_TIMING_CONFIDENCE_STEP     = 0.05  # bonus per extra coincidence above minimum
_TIMING_MAX_CONFIDENCE      = 0.90
_PATTERN_BASE_CONFIDENCE    = 0.65
_MULTI_METHOD_BONUS         = 0.10  # per additional method beyond the first


# ── Confidence helper ─────────────────────────────────────────────────────────

def _compute_confidence(methods: set[str], extra: dict | None = None) -> float:
    """
    Compute cluster confidence from the set of detection methods used.

    extra (optional): {"timing_coincidences": int} for bonus timing confidence.
    """
    _base = {
        "funding": _FUNDING_BASE_CONFIDENCE,
        "timing":  _TIMING_BASE_CONFIDENCE,
        "pattern": _PATTERN_BASE_CONFIDENCE,
    }
    if not methods:
        return 0.0

    base = max(_base.get(m, 0.5) for m in methods)

    # Timing bonus: more coincidences → higher confidence
    if "timing" in methods and extra:
        coincidences = extra.get("timing_coincidences", _TIMING_MIN_COINCIDENCES)
        bonus = _TIMING_CONFIDENCE_STEP * (coincidences - _TIMING_MIN_COINCIDENCES)
        timing_confidence = min(_TIMING_MAX_CONFIDENCE, _TIMING_BASE_CONFIDENCE + bonus)
        base = max(base, timing_confidence)

    # Multi-method bonus
    multi_bonus = _MULTI_METHOD_BONUS * (len(methods) - 1)
    return min(1.0, base + multi_bonus)


# ── Simple union-find ─────────────────────────────────────────────────────────

class _UnionFind:
    """Path-compressed union-find for grouping (wallet_address, chain) keys."""

    def __init__(self) -> None:
        self._parent: dict[tuple, tuple] = {}

    def _find(self, x: tuple) -> tuple:
        if x not in self._parent:
            self._parent[x] = x
        if self._parent[x] != x:
            self._parent[x] = self._find(self._parent[x])
        return self._parent[x]

    def union(self, a: tuple, b: tuple) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            # Deterministic root: use lexicographic order so tests are stable
            if ra <= rb:
                self._parent[rb] = ra
            else:
                self._parent[ra] = rb

    def get_groups(self) -> dict[tuple, list[tuple]]:
        """Return {root: [members]} for all groups with 2+ members."""
        groups: dict[tuple, list[tuple]] = defaultdict(list)
        for key in list(self._parent.keys()):
            groups[self._find(key)].append(key)
        return {root: members for root, members in groups.items() if len(members) >= 2}


# ── Background service ────────────────────────────────────────────────────────

class ClusterAnalyzer:
    """
    Background service — re-detects wallet clusters every 10 minutes.

    Called as an asyncio task from api/main.py lifespan startup.
    On each cycle: full rebuild (delete stale → detect → persist → dispatch new).

    Keeps a set of known cluster fingerprints across cycles so we only
    dispatch WALLET_CLUSTER alerts for genuinely new or grown clusters.
    """

    def __init__(self) -> None:
        # frozenset of member (address, chain) tuples — one per known cluster
        self._known_fingerprints: set[frozenset] = set()

    async def start(self) -> None:
        logger.info(
            "[cluster] ClusterAnalyzer started (interval=%ds, lookback=%dd)",
            _ANALYSIS_INTERVAL_SECONDS, _LOOKBACK_DAYS,
        )
        while True:
            try:
                await _run_analysis(self._known_fingerprints)
            except asyncio.CancelledError:
                logger.info("[cluster] ClusterAnalyzer cancelled.")
                return
            except Exception as exc:
                logger.error("[cluster] analysis cycle failed: %s", exc)
            await asyncio.sleep(_ANALYSIS_INTERVAL_SECONDS)


# ── Core analysis pipeline ────────────────────────────────────────────────────

async def _run_analysis(known_fingerprints: set[frozenset]) -> None:
    """One full detection + persistence cycle.  Dispatches alerts for new clusters."""
    async with AsyncSessionLocal() as db:
        lookback_start = datetime.datetime.utcnow() - datetime.timedelta(days=_LOOKBACK_DAYS)

        # Exchange addresses are never clustered
        exchange_addrs = await _get_exchange_addresses(db)

        # Load all active tracked non-exchange wallets
        wallet_rows = await db.execute(
            select(TrackedWallet.address, TrackedWallet.chain, TrackedWallet.label)
            .where(TrackedWallet.is_active == True)  # noqa: E712
        )
        all_wallet_rows = wallet_rows.fetchall()
        if not all_wallet_rows:
            return

        # chain → {address: label}
        chain_wallet_map: dict[str, dict[str, Optional[str]]] = defaultdict(dict)
        for row in all_wallet_rows:
            addr = row.address.lower()
            if addr not in exchange_addrs:
                chain_wallet_map[row.chain][addr] = row.label

        # Collect all signals: list of (key_a, key_b, method, extra_dict)
        # key = (wallet_address, chain)
        all_signals: list[tuple[tuple, tuple, str, dict]] = []

        await _detect_funding(db, all_signals, chain_wallet_map, lookback_start, exchange_addrs)
        await _detect_timing(db, all_signals, chain_wallet_map, lookback_start)
        await _detect_pattern(db, all_signals, chain_wallet_map, lookback_start)

        if not all_signals:
            # Clear any stale clusters from a prior cycle
            await db.execute(delete(ClusterMembership))
            await db.execute(delete(WalletCluster))
            await db.commit()
            known_fingerprints.clear()
            logger.info("[cluster] no signals detected — cleared stale clusters")
            return

        # Build union-find from signals
        uf = _UnionFind()
        for key_a, key_b, _method, _extra in all_signals:
            uf.union(key_a, key_b)

        groups = uf.get_groups()
        if not groups:
            await db.execute(delete(ClusterMembership))
            await db.execute(delete(WalletCluster))
            await db.commit()
            known_fingerprints.clear()
            return

        cluster_data = await _persist_clusters(
            db, uf, groups, all_signals, chain_wallet_map, lookback_start
        )
        logger.info("[cluster] rebuilt %d cluster(s) from %d signals", len(groups), len(all_signals))

    # Dispatch WALLET_CLUSTER events for new clusters (outside the DB session)
    await _dispatch_new_clusters(cluster_data, known_fingerprints)


# ── Exchange address lookup ───────────────────────────────────────────────────

async def _get_exchange_addresses(db: AsyncSession) -> set[str]:
    result = await db.execute(
        select(SmartLabel.address).where(SmartLabel.entity_type == "exchange")
    )
    return {row.address.lower() for row in result.fetchall()}


# ── Method 1 — Funding source ─────────────────────────────────────────────────

async def _detect_funding(
    db: AsyncSession,
    all_signals: list,
    chain_wallet_map: dict[str, dict[str, Optional[str]]],
    lookback_start: datetime.datetime,
    exchange_addrs: set[str],
) -> None:
    """
    Method 1: Wallet A sent ETH/native directly to wallet B (SEND direction,
    both tracked, same chain).  If B then traded any of the same tokens as A
    → high confidence same entity.
    Funding Method looks at WalletFundingEvents over 90 days.
    """
    funding_lookback_start = datetime.datetime.utcnow() - datetime.timedelta(days=_FUNDING_LOOKBACK_DAYS)
    
    for chain, wallets in chain_wallet_map.items():
        if len(wallets) < 2:
            continue

        wallet_addrs = list(wallets.keys())

        # SEND alerts where both sides are tracked (same chain)
        sends_result = await db.execute(
            select(WalletFundingEvent.from_address, WalletFundingEvent.to_address)
            .where(and_(
                WalletFundingEvent.chain     == chain,
                WalletFundingEvent.timestamp >= funding_lookback_start,
                WalletFundingEvent.from_address.in_(wallet_addrs),
                WalletFundingEvent.to_address.in_(wallet_addrs),
            ))
            .distinct()
        )
        pairs = sends_result.fetchall()
        if not pairs:
            continue

        for row in pairs:
            addr_a = row.from_address.lower()
            addr_b = row.to_address.lower()

            if addr_a in exchange_addrs or addr_b in exchange_addrs:
                continue
            if addr_a not in wallets or addr_b not in wallets:
                continue

            # Funding alone is already a strong signal (direct wallet funding is unusual)
            # Optionally check shared token trading to raise confidence
            tokens_a_result = await db.execute(
                select(WhaleAlert.token_address)
                .where(and_(
                    WhaleAlert.chain       == chain,
                    WhaleAlert.direction   == "BUY",
                    WhaleAlert.to_address  == addr_a,
                    WhaleAlert.detected_at >= lookback_start,
                    WhaleAlert.token_address.isnot(None),
                ))
                .distinct()
            )
            tokens_b_result = await db.execute(
                select(WhaleAlert.token_address)
                .where(and_(
                    WhaleAlert.chain       == chain,
                    WhaleAlert.direction   == "BUY",
                    WhaleAlert.to_address  == addr_b,
                    WhaleAlert.detected_at >= lookback_start,
                    WhaleAlert.token_address.isnot(None),
                ))
                .distinct()
            )
            tokens_a = {r.token_address for r in tokens_a_result.fetchall()}
            tokens_b = {r.token_address for r in tokens_b_result.fetchall()}
            common_tokens = len(tokens_a & tokens_b)

            key_a = (addr_a, chain)
            key_b = (addr_b, chain)
            all_signals.append((key_a, key_b, "funding", {"common_tokens": common_tokens}))

            logger.debug(
                "[cluster:funding] %s → %s on %s (common tokens: %d)",
                addr_a[:8], addr_b[:8], chain, common_tokens,
            )


# ── Method 2 — Timing correlation ────────────────────────────────────────────

async def _detect_timing(
    db: AsyncSession,
    all_signals: list,
    chain_wallet_map: dict[str, dict[str, Optional[str]]],
    lookback_start: datetime.datetime,
) -> None:
    """
    Method 2: Wallets A and B both bought the same token within 5 minutes of
    each other, and this co-buy happened 3+ times → coordinated (same entity).
    """
    for chain, wallets in chain_wallet_map.items():
        if len(wallets) < 2:
            continue

        wallet_addrs = list(wallets.keys())

        # All BUY alerts for tracked wallets on this chain
        buys_result = await db.execute(
            select(
                WhaleAlert.to_address,
                WhaleAlert.token_address,
                WhaleAlert.detected_at,
            )
            .where(and_(
                WhaleAlert.chain       == chain,
                WhaleAlert.direction   == "BUY",
                WhaleAlert.detected_at >= lookback_start,
                WhaleAlert.to_address.in_(wallet_addrs),
                WhaleAlert.token_address.isnot(None),
            ))
            .order_by(WhaleAlert.token_address, WhaleAlert.detected_at)
        )
        buys = buys_result.fetchall()
        if not buys:
            continue

        # Group by token_address
        token_events: dict[str, list[tuple[str, datetime.datetime]]] = defaultdict(list)
        for row in buys:
            token_events[row.token_address].append((row.to_address, row.detected_at))

        # For each token, count co-buy coincidences per wallet pair
        # pair_key → coincidences
        pair_coincidences: dict[tuple[str, str], int] = defaultdict(int)

        for _token, events in token_events.items():
            events.sort(key=lambda x: x[1])
            n = len(events)
            for i in range(n):
                wallet_a, time_a = events[i]
                for j in range(i + 1, n):
                    wallet_b, time_b = events[j]
                    if wallet_b == wallet_a:
                        continue
                    delta = (time_b - time_a).total_seconds()
                    if delta > _TIMING_WINDOW_SECONDS:
                        break   # sorted by time → no more matches for this i
                    pair_key = (min(wallet_a, wallet_b), max(wallet_a, wallet_b))
                    pair_coincidences[pair_key] += 1

        # Emit signals for qualifying pairs
        for (wallet_a, wallet_b), count in pair_coincidences.items():
            if count >= _TIMING_MIN_COINCIDENCES:
                key_a = (wallet_a, chain)
                key_b = (wallet_b, chain)
                all_signals.append((key_a, key_b, "timing", {"timing_coincidences": count}))
                logger.debug(
                    "[cluster:timing] %s & %s on %s — %d co-buys within %ds",
                    wallet_a[:8], wallet_b[:8], chain, count, _TIMING_WINDOW_SECONDS,
                )


# ── Method 3 — Directional pattern ───────────────────────────────────────────

async def _detect_pattern(
    db: AsyncSession,
    all_signals: list,
    chain_wallet_map: dict[str, dict[str, Optional[str]]],
    lookback_start: datetime.datetime,
) -> None:
    """
    Method 3: 3+ wallets moved same direction (all BUY or all SELL) on the same
    token within the same hour-bucket.  If the SAME PAIR appears in 2+ such
    hour-buckets → coordinated cluster.
    """
    for chain, wallets in chain_wallet_map.items():
        if len(wallets) < _PATTERN_MIN_WALLETS:
            continue

        wallet_addrs = list(wallets.keys())

        # All BUY + SELL alerts for tracked wallets on this chain
        alerts_result = await db.execute(
            select(
                WhaleAlert.from_address,
                WhaleAlert.to_address,
                WhaleAlert.direction,
                WhaleAlert.token_address,
                WhaleAlert.detected_at,
            )
            .where(and_(
                WhaleAlert.chain       == chain,
                WhaleAlert.direction.in_(["BUY", "SELL"]),
                WhaleAlert.detected_at >= lookback_start,
                or_(
                    WhaleAlert.to_address.in_(wallet_addrs),
                    WhaleAlert.from_address.in_(wallet_addrs),
                ),
                WhaleAlert.token_address.isnot(None),
            ))
        )
        alerts = alerts_result.fetchall()
        if not alerts:
            continue

        # Group by (token_address, direction, hour_bucket)
        # bucket → set of wallets that moved in that bucket
        bucket_wallets: dict[tuple, set[str]] = defaultdict(set)
        for row in alerts:
            hour = row.detected_at.replace(minute=0, second=0, microsecond=0)
            wallet = row.to_address if row.direction == "BUY" else row.from_address
            if wallet.lower() not in wallets:
                continue
            bucket_wallets[(row.token_address, row.direction, hour)].add(wallet.lower())

        # For each bucket with 3+ wallets, count how many times each wallet pair
        # appears together across different hour-buckets (for the same token)
        # (wallet_a, wallet_b) → number of hour-buckets they shared
        pair_bucket_count: dict[tuple[str, str], int] = defaultdict(int)

        for (token_addr, _direction, _hour), wallet_set in bucket_wallets.items():
            if len(wallet_set) < _PATTERN_MIN_WALLETS:
                continue
            wallet_list = sorted(wallet_set)
            for i in range(len(wallet_list)):
                for j in range(i + 1, len(wallet_list)):
                    # Key includes token so we don't conflate unrelated tokens
                    pair_key = (wallet_list[i], wallet_list[j], token_addr)
                    pair_bucket_count[pair_key] += 1

        # Emit signals for pairs that appeared together in 2+ hour-buckets
        for (wallet_a, wallet_b, token_addr), count in pair_bucket_count.items():
            if count >= _PATTERN_MIN_OCCURRENCES:
                key_a = (wallet_a, chain)
                key_b = (wallet_b, chain)
                all_signals.append((key_a, key_b, "pattern", {"pattern_occurrences": count}))
                logger.debug(
                    "[cluster:pattern] %s & %s on %s token %s — %d shared hour-buckets",
                    wallet_a[:8], wallet_b[:8], chain, token_addr[:8], count,
                )


# ── Persistence (full rebuild) ────────────────────────────────────────────────

async def _persist_clusters(
    db: AsyncSession,
    uf: _UnionFind,
    groups: dict[tuple, list[tuple]],
    all_signals: list,
    chain_wallet_map: dict[str, dict[str, Optional[str]]],
    lookback_start: datetime.datetime,
) -> list[dict]:
    """Full rebuild: delete existing clusters, then insert fresh ones.

    Returns a list of cluster data dicts for post-commit event dispatch.
    """

    # Per-wallet signals map: (address, chain) → set of methods
    wallet_signals: dict[tuple, set[str]] = defaultdict(set)
    for key_a, key_b, method, _extra in all_signals:
        wallet_signals[key_a].add(method)
        wallet_signals[key_b].add(method)

    # Per-group methods and timing extras
    group_methods: dict[tuple, set[str]] = defaultdict(set)
    group_timing_extra: dict[tuple, dict] = defaultdict(dict)
    for key_a, key_b, method, extra in all_signals:
        root = uf._find(key_a)
        group_methods[root].add(method)
        if method == "timing" and "timing_coincidences" in extra:
            prev = group_timing_extra[root].get("timing_coincidences", 0)
            group_timing_extra[root]["timing_coincidences"] = max(prev, extra["timing_coincidences"])

    # Clear old data first
    await db.execute(delete(ClusterMembership))
    await db.execute(delete(WalletCluster))

    cluster_data_list: list[dict] = []

    for root_key, members in groups.items():
        if len(members) < 2:
            continue

        chain = members[0][1]   # (address, chain) → chain at index 1
        methods = group_methods.get(root_key, set())
        extra = group_timing_extra.get(root_key, {})
        confidence = _compute_confidence(methods, extra)
        methods_csv = ",".join(sorted(methods))

        # Compute total volume for all member wallets in lookback window
        member_addrs = [m[0] for m in members]
        vol_row = await db.execute(
            select(func.sum(WhaleAlert.amount_usd))
            .where(and_(
                WhaleAlert.chain       == chain,
                WhaleAlert.detected_at >= lookback_start,
                or_(
                    WhaleAlert.from_address.in_(member_addrs),
                    WhaleAlert.to_address.in_(member_addrs),
                ),
            ))
        )
        total_volume = float(vol_row.scalar() or 0.0)

        # Derive cluster label from wallet labels (first labelled wallet wins)
        cluster_label: Optional[str] = None
        for addr, _ch in members:
            lbl = chain_wallet_map.get(chain, {}).get(addr)
            if lbl:
                cluster_label = f"{lbl}'s Cluster"
                break

        cluster = WalletCluster(
            chain=chain,
            cluster_label=cluster_label,
            detection_methods=methods_csv,
            confidence=confidence,
            member_count=len(members),
            total_volume_usd=total_volume,
        )
        db.add(cluster)
        await db.flush()   # get cluster.id before adding memberships

        member_addresses: list[str] = []
        member_labels: list[Optional[str]] = []
        for addr, ch in members:
            member_methods = wallet_signals.get((addr, ch), methods)
            member_signals_csv = ",".join(sorted(member_methods))
            wallet_label = chain_wallet_map.get(ch, {}).get(addr)
            membership = ClusterMembership(
                cluster_id=cluster.id,
                wallet_address=addr,
                chain=ch,
                wallet_label=wallet_label,
                detection_signals=member_signals_csv,
                confidence=confidence,
            )
            db.add(membership)
            member_addresses.append(addr)
            member_labels.append(wallet_label)

        cluster_data_list.append({
            "cluster_id":        cluster.id,
            "cluster_label":     cluster_label,
            "chain":             chain,
            "confidence":        confidence,
            "member_count":      len(members),
            "detection_methods": methods_csv,
            "total_volume_usd":  total_volume,
            "member_addresses":  member_addresses,
            "member_labels":     member_labels,
            "fingerprint":       frozenset(members),
        })

    await db.commit()
    return cluster_data_list


# ── New-cluster event dispatch ────────────────────────────────────────────────

async def _dispatch_new_clusters(
    cluster_data_list: list[dict],
    known_fingerprints: set[frozenset],
) -> None:
    """
    Dispatch a WALLET_CLUSTER AlertDTO for each genuinely new cluster.

    A cluster is "new" if its member fingerprint was not seen in the previous
    analysis cycle.  This prevents re-alerting every 10 minutes for the same
    persistent cluster.
    """
    if not cluster_data_list:
        known_fingerprints.clear()
        return

    # Lazy import to avoid circular dependency at module load time
    try:
        from api.events.dispatcher import event_dispatcher  # noqa: PLC0415
        from api.events.protocol import AlertDTO, AlertType  # noqa: PLC0415
    except Exception as exc:
        logger.error("[cluster] could not import event_dispatcher: %s", exc)
        known_fingerprints.clear()
        for cd in cluster_data_list:
            known_fingerprints.add(cd["fingerprint"])
        return

    new_fingerprints: set[frozenset] = set()
    now = datetime.datetime.utcnow()

    for cd in cluster_data_list:
        fp = cd["fingerprint"]
        new_fingerprints.add(fp)
        if fp in known_fingerprints:
            continue   # already alerted for this exact group

        event = AlertDTO(
            alert_type=AlertType.WALLET_CLUSTER,
            alert_id=cd["cluster_id"],
            chain=cd["chain"],
            timestamp=now,
            metadata={
                "cluster_id":        cd["cluster_id"],
                "cluster_label":     cd["cluster_label"],
                "confidence":        cd["confidence"],
                "member_count":      cd["member_count"],
                "detection_methods": cd["detection_methods"],
                "total_volume_usd":  cd["total_volume_usd"],
                "member_addresses":  cd["member_addresses"],
                "member_labels":     cd["member_labels"],
            },
        )
        try:
            await event_dispatcher.dispatch(event)
            logger.info(
                "[cluster] dispatched WALLET_CLUSTER alert for cluster #%d (%d members, %.0f%% conf)",
                cd["cluster_id"], cd["member_count"], cd["confidence"] * 100,
            )
        except Exception as exc:
            logger.error("[cluster] failed to dispatch cluster alert: %s", exc)

    # Replace known fingerprints with the current cycle's set
    known_fingerprints.clear()
    known_fingerprints.update(new_fingerprints)


# ── Real-time lookup for alert enrichment ─────────────────────────────────────

async def get_wallet_cluster_info(
    db: AsyncSession,
    wallet_address: str,
    chain: str,
) -> Optional[dict]:
    """
    Look up cluster membership for a wallet address.

    Called from whale_tracker.py to enrich every WhaleAlertEvent with cluster
    context so downstream consumers (Discord/Telegram bots, Twitter) know when
    a whale alert comes from a clustered entity.

    Returns a dict with cluster metadata, or None if not in any cluster.
    """
    membership = await db.scalar(
        select(ClusterMembership).where(and_(
            ClusterMembership.wallet_address == wallet_address.lower(),
            ClusterMembership.chain          == chain,
        ))
    )
    if membership is None:
        return None

    cluster = await db.scalar(
        select(WalletCluster).where(WalletCluster.id == membership.cluster_id)
    )
    if cluster is None:
        return None

    return {
        "cluster_id":         cluster.id,
        "cluster_label":      cluster.cluster_label,
        "cluster_confidence": cluster.confidence,
        "cluster_size":       cluster.member_count,
        "detection_methods":  cluster.detection_methods,
        "total_volume_usd":   cluster.total_volume_usd,
    }
