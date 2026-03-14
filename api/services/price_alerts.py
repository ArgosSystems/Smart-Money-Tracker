"""
api/services/price_alerts.py
-----------------------------
Price helpers + trending-token query (chain-aware) + PriceAlertChecker.

PriceAlertChecker
-----------------
Runs as a background asyncio task started in api/main.py lifespan.
Every CHECK_INTERVAL seconds it:
  1. Loads all active PriceAlertRule rows from DB.
  2. Groups them by (chain, token_address) to batch CoinGecko calls.
  3. For each rule whose price condition is met (and cooldown elapsed),
     fires a broadcast via alert_broadcaster and updates last_triggered_at.

Cooldown: 1 hour between repeated triggers of the same rule.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

import httpx
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AsyncSessionLocal, PriceAlertRule, TokenActivity
from api.events.dispatcher import event_dispatcher
from api.events.types import PriceTriggerEvent
from config.chains import CHAINS

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60        # seconds between price checks
TRIGGER_COOLDOWN = 3600    # seconds before the same rule can trigger again


# ── Price fetch helpers ───────────────────────────────────────────────────────

async def fetch_token_price(token_address: str, coingecko_platform: str = "ethereum") -> float:
    """Return current USD price of an ERC-20 token via CoinGecko."""
    url = (
        f"https://api.coingecko.com/api/v3/simple/token_price/{coingecko_platform}"
        f"?contract_addresses={token_address.lower()}&vs_currencies=usd"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json().get(token_address.lower(), {}).get("usd", 0.0)
    except Exception as exc:
        logger.warning("Price fetch failed for %s on %s: %s", token_address, coingecko_platform, exc)
        return 0.0


async def fetch_prices_batch(
    token_addresses: list[str],
    coingecko_platform: str,
) -> dict[str, float]:
    """Fetch USD prices for multiple tokens in a single CoinGecko call."""
    if not token_addresses:
        return {}
    joined = ",".join(a.lower() for a in token_addresses)
    url = (
        f"https://api.coingecko.com/api/v3/simple/token_price/{coingecko_platform}"
        f"?contract_addresses={joined}&vs_currencies=usd"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return {addr.lower(): info.get("usd", 0.0) for addr, info in data.items()}
    except Exception as exc:
        logger.warning("Batch price fetch failed on %s: %s", coingecko_platform, exc)
        return {}


# ── Trending helper ───────────────────────────────────────────────────────────

async def get_trending_tokens(
    db: AsyncSession,
    chain: Optional[str] = None,
    limit: int = 10,
) -> list[TokenActivity]:
    """Top tokens by whale buy count. Optionally filtered to a single chain."""
    query = (
        select(TokenActivity)
        .where(TokenActivity.buy_count > 0)
        .order_by(desc(TokenActivity.buy_count))
        .limit(limit)
    )
    if chain:
        query = query.where(TokenActivity.chain == chain.lower())

    result = await db.execute(query)
    return list(result.scalars().all())


# ── PriceAlertChecker ─────────────────────────────────────────────────────────

class PriceAlertChecker:
    """
    Background task that periodically checks token prices against user-defined
    PriceAlertRule entries and broadcasts matches via alert_broadcaster.
    """

    async def start(self) -> None:
        """Run forever, checking prices every CHECK_INTERVAL seconds."""
        logger.info("PriceAlertChecker started (interval=%ds)", CHECK_INTERVAL)
        while True:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                logger.info("PriceAlertChecker cancelled.")
                return
            except Exception as exc:
                logger.error("PriceAlertChecker error: %s", exc)
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_all(self) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PriceAlertRule).where(PriceAlertRule.is_active == True)  # noqa: E712
            )
            rules: list[PriceAlertRule] = list(result.scalars().all())

        if not rules:
            return

        # Group rules by chain so we make one CoinGecko call per chain
        by_chain: dict[str, list[PriceAlertRule]] = {}
        for rule in rules:
            by_chain.setdefault(rule.chain, []).append(rule)

        for chain_name, chain_rules in by_chain.items():
            chain_cfg = CHAINS.get(chain_name)
            if not chain_cfg:
                continue

            token_addresses = list({r.token_address.lower() for r in chain_rules})
            prices = await fetch_prices_batch(token_addresses, chain_cfg.coingecko_platform)

            if not prices:
                continue

            now = datetime.datetime.utcnow()
            async with AsyncSessionLocal() as db:
                for rule in chain_rules:
                    price = prices.get(rule.token_address.lower(), 0.0)
                    if price == 0.0:
                        continue

                    # Check cooldown
                    if rule.last_triggered_at:
                        elapsed = (now - rule.last_triggered_at).total_seconds()
                        if elapsed < TRIGGER_COOLDOWN:
                            continue

                    # Check condition
                    triggered = (
                        (rule.condition == "above" and price >= rule.target_price_usd) or
                        (rule.condition == "below" and price <= rule.target_price_usd)
                    )
                    if not triggered:
                        continue

                    logger.info(
                        "Price alert triggered: %s %s %s $%.4f (current=$%.4f)",
                        chain_name, rule.token_symbol, rule.condition,
                        rule.target_price_usd, price,
                    )

                    # Broadcast to all registered plugins (WebSocket, Twitter, etc.)
                    await event_dispatcher.dispatch(PriceTriggerEvent(
                        alert_id=rule.id,
                        chain=rule.chain,
                        timestamp=now,
                        metadata={
                            "type":              "price_alert",
                            "rule_id":           rule.id,
                            "token_address":     rule.token_address,
                            "token_symbol":      rule.token_symbol,
                            "condition":         rule.condition,
                            "target_price_usd":  rule.target_price_usd,
                            "current_price_usd": price,
                            "label":             rule.label,
                            "triggered_at":      now.isoformat(),
                            "pct_change_24h":    None,
                        },
                    ))

                    # Update last_triggered_at in DB
                    rule_db = await db.get(PriceAlertRule, rule.id)
                    if rule_db:
                        rule_db.last_triggered_at = now
                        db.add(rule_db)

                await db.commit()

