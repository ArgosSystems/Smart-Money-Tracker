"""
bots/discord_bot/auto_push.py
-------------------------------
Background task that connects to the API WebSocket, receives real-time
alerts, and auto-posts them to configured Discord channels.

Lifecycle
---------
start_auto_push(bot)  — called once from on_ready
stop_auto_push()      — called on bot shutdown

The task reconnects automatically on disconnect with exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import discord
from discord.ext import commands

from config.settings import settings

from ._shared import (
    CHAIN_EMOJI,
    api_get,
    build_cv2,
    chain_badge,
    chain_color,
    dir_emoji,
    fmt_usd,
    short_addr,
    tx_link,
    COLOR_BUY,
    COLOR_SELL,
    COLOR_INFO,
)

logger = logging.getLogger(__name__)

_push_task: asyncio.Task | None = None
_MAX_BACKOFF = 60  # seconds


def _branding_line() -> str:
    """One visible line shown at the bottom of every alert body."""
    name = settings.brand_name
    parts: list[str] = []
    if settings.brand_discord_invite:
        parts.append(f"[Join]({settings.brand_discord_invite})")
    if settings.brand_github_url:
        parts.append(f"[GitHub]({settings.brand_github_url})")
    links = " · ".join(parts)
    return f"🤖 *Automated alert by **{name}***" + (f"  —  {links}" if links else "")


def _score_footer(score: float) -> str:
    return f"Priority score: {score:.0f}/100"


def _format_whale_alert(data: dict, tier: str = "free") -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a whale alert dict."""
    chain = data.get("chain", "ethereum")
    symbol = data.get("token_symbol") or "ETH"
    direction = data.get("direction", "SEND")
    amount_usd = data.get("amount_usd", 0.0)
    amount_token = data.get("amount_token", 0.0)
    tx_hash = data.get("tx_hash", "")
    from_addr = data.get("from_address", "")
    to_addr = data.get("to_address", "")
    block = data.get("block_number", 0)
    score = data.get("priority_score", 0)

    def get_display_label(manual: str, smart_name: str, smart_tier: str, guild_tier: str) -> str:
        if manual:
            return manual
        if smart_name:
            if smart_tier == "free" or guild_tier == "pro":
                return f"{smart_name}"
            else:
                return "🤖 Smart Entity (Pro 🔒)"
        return ""

    from_label = get_display_label(
        data.get("wallet_label") or data.get("from_label"),
        data.get("from_smart_label_name"),
        data.get("from_smart_label_tier"),
        tier
    )

    to_label = get_display_label(
        data.get("to_label"),
        data.get("to_smart_label_name"),
        data.get("to_smart_label_tier"),
        tier
    )

    c_emoji = CHAIN_EMOJI.get(chain, "")
    link = tx_link(tx_hash, chain) if tx_hash else ""

    from_str = f"`{short_addr(from_addr)}`"
    if from_label:
        from_str += f" **({from_label})**"

    to_str = f"`{short_addr(to_addr)}`"
    if to_label:
        to_str += f" **({to_label})**"

    color = COLOR_BUY if direction == "BUY" else COLOR_SELL if direction == "SELL" else COLOR_INFO
    d_emoji = dir_emoji(direction)

    lines = [
        f"**{d_emoji} {direction}  ·  {symbol}  ·  {fmt_usd(amount_usd)}** {c_emoji}",
        f"📦 `{amount_token:,.4f} {symbol}`",
        f"👤 From: {from_str}",
        f"📬 To:     {to_str}",
    ]
    if link:
        lines.append(f"🔗 [View transaction ↗]({link})  ·  Block `{block}`")
    lines.append(_branding_line())

    title = f"🐋 Whale Alert  ·  {chain_badge(chain)}"
    footer = _score_footer(score)
    return title, lines, color, footer


def _format_accumulation_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from an accumulation alert dict."""
    wallet  = data.get("wallet_address", "")
    symbol  = data.get("token_symbol") or "Unknown"
    chain   = data.get("chain", "ethereum")
    buys    = data.get("buy_count", 0)
    total   = data.get("total_usd", 0.0)
    avg     = data.get("avg_per_tx_usd", 0.0)
    window  = data.get("window_hours", 24)
    label   = data.get("wallet_label")

    wallet_str = f"`{short_addr(wallet)}`"
    if label:
        wallet_str += f" **({label})**"

    c_emoji = CHAIN_EMOJI.get(chain, "")
    lines = [
        f"**🔁 {symbol}  ·  {buys}× buys in {window}h** {c_emoji}",
        f"👤 Wallet: {wallet_str}",
        f"💰 Total:  **{fmt_usd(total)}**  ·  Avg/tx: {fmt_usd(avg)}",
        _branding_line(),
    ]
    title  = f"📈 Accumulation Alert  ·  {chain_badge(chain)}"
    footer = f"Repeated buy pattern detected over {window}h window"
    return title, lines, COLOR_BUY, footer


def _format_price_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a price alert dict."""
    token = data.get("token_id", "unknown")
    price = data.get("current_price", 0.0)
    target = data.get("target_price", 0.0)
    condition = data.get("condition", "above")
    pct_change = data.get("pct_change_24h", 0.0)

    emoji = "🚀" if condition == "above" else "📉"
    direction_text = "broke above" if condition == "above" else "dropped below"
    lines = [
        f"**{emoji} {token.upper()}  ·  {direction_text} target**",
        f"💵 Current price:  **${price:,.6f}**",
        f"🎯 Your target:    **${target:,.6f}**",
    ]
    if pct_change:
        change_emoji = "📈" if pct_change > 0 else "📉"
        lines.append(f"{change_emoji} 24h change:  **{pct_change:+.2f}%**")
    lines.append(_branding_line())

    color = COLOR_BUY if condition == "above" else COLOR_SELL
    return f"💰 Price Alert  ·  {token.upper()}", lines, color, "Price target reached"


def _matches_channel_config(data: dict, channel_cfg: dict) -> bool:
    """Check if an alert matches a channel's filter configuration."""
    # Min score filter
    min_score = channel_cfg.get("min_score", 0.0)
    if min_score > 0:
        score = data.get("priority_score", 0) or 0
        if score < min_score:
            return False

    # Chain filter
    chains_csv = channel_cfg.get("chains")
    if chains_csv:
        allowed = {c.strip().lower() for c in chains_csv.split(",") if c.strip()}
        if allowed and data.get("chain", "").lower() not in allowed:
            return False

    # Alert type filter
    types_csv = channel_cfg.get("alert_types")
    if types_csv:
        allowed_types = {t.strip().lower() for t in types_csv.split(",") if t.strip()}
        if allowed_types and data.get("alert_type", "whale").lower() not in allowed_types:
            return False

    return True


async def _push_loop(bot: commands.Bot) -> None:
    """Main loop: connect to WebSocket, receive alerts, post to channels."""
    backoff = 1
    ws_url = settings.ws_url.rstrip("/") + "/ws/alerts"

    while True:
        try:
            logger.info("Auto-push: connecting to %s", ws_url)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    logger.info("Auto-push: connected to WebSocket")
                    backoff = 1  # reset on successful connect

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            await _dispatch_alert(bot, data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        except asyncio.CancelledError:
            logger.info("Auto-push: task cancelled")
            return
        except Exception as exc:
            logger.warning("Auto-push: connection error: %s", exc)

        logger.info("Auto-push: reconnecting in %ds…", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _MAX_BACKOFF)


async def _dispatch_alert(bot: commands.Bot, data: dict) -> None:
    """Send an alert to all matching configured channels."""
    # Fetch active channel configs from API
    channels = await api_get("/alert-channels/active")
    if not isinstance(channels, list) or not channels:
        return

    # Format the alert based on type
    alert_type = data.get("alert_type", "whale")

    free_view, pro_view, fallback_view = None, None, None

    if alert_type == "whale":
        free_title, free_lines, free_color, free_footer = _format_whale_alert(data, tier="free")
        pro_title, pro_lines, pro_color, pro_footer = _format_whale_alert(data, tier="pro")
        free_view = build_cv2(title=free_title, lines=free_lines, color=free_color, footer=free_footer)
        pro_view = build_cv2(title=pro_title, lines=pro_lines, color=pro_color, footer=pro_footer)
    elif alert_type == "price":
        title, lines, color, footer = _format_price_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    elif alert_type == "accumulation":
        title, lines, color, footer = _format_accumulation_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    else:
        return  # portfolio alerts not pushed to Discord

    for cfg in channels:
        if not _matches_channel_config(data, cfg):
            continue

        channel_id = int(cfg["channel_id"])
        guild_tier = cfg.get("guild_tier", "free")
        
        if alert_type == "whale":
            view_to_send = pro_view if guild_tier == "pro" else free_view
        else:
            view_to_send = fallback_view

        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(view=view_to_send)
        except discord.Forbidden:
            logger.warning(
                "Auto-push: missing permissions for channel %s (guild %s)",
                channel_id, cfg.get("guild_id"),
            )
        except discord.NotFound:
            logger.warning("Auto-push: channel %s not found", channel_id)
        except Exception as exc:
            logger.error("Auto-push: failed to send to channel %s: %s", channel_id, exc)


async def start_auto_push(bot: commands.Bot) -> None:
    """Start the auto-push background task. Call from on_ready."""
    global _push_task
    if _push_task and not _push_task.done():
        return
    _push_task = asyncio.create_task(_push_loop(bot), name="discord_auto_push")
    logger.info("Auto-push: background task started")


async def stop_auto_push() -> None:
    """Cancel the auto-push task. Call on bot shutdown."""
    global _push_task
    if _push_task and not _push_task.done():
        _push_task.cancel()
        try:
            await _push_task
        except asyncio.CancelledError:
            pass
    _push_task = None
