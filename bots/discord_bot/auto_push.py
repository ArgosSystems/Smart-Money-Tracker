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

Handled alert types
-------------------
  whale          → tier-aware (free/pro smart labels)
  price          → price target reached
  accumulation   → repeated buy pattern
  exchange_flow  → OUTFLOW 🔴 / INFLOW 🟢 whale ↔ exchange
  wallet_cluster → coordinated wallet group detected
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
    COLOR_WARN,
)

logger = logging.getLogger(__name__)

_push_task: asyncio.Task | None = None
_MAX_BACKOFF = 60  # seconds


def _branding_line() -> str:
    """One visible line shown at the bottom of every alert body."""
    name  = settings.brand_name
    parts: list[str] = []
    if settings.brand_discord_invite:
        parts.append(f"[Join]({settings.brand_discord_invite})")
    if settings.brand_github_url:
        parts.append(f"[GitHub]({settings.brand_github_url})")
    links = " · ".join(parts)
    return f"🤖 *Automated alert by **{name}***" + (f"  —  {links}" if links else "")


def _score_footer(score: float) -> str:
    return f"Priority score: {score:.0f}/100"


# ── Alert formatters ──────────────────────────────────────────────────────────

def _format_whale_alert(data: dict, tier: str = "free") -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a whale alert dict."""
    chain        = data.get("chain", "ethereum")
    symbol       = data.get("token_symbol") or "ETH"
    direction    = data.get("direction", "SEND")
    amount_usd   = data.get("amount_usd", 0.0)
    amount_token = data.get("amount_token", 0.0)
    tx_hash      = data.get("tx_hash", "")
    from_addr    = data.get("from_address", "")
    to_addr      = data.get("to_address", "")
    block        = data.get("block_number", 0)
    score        = data.get("priority_score", 0)

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
        tier,
    )
    to_label = get_display_label(
        data.get("to_label"),
        data.get("to_smart_label_name"),
        data.get("to_smart_label_tier"),
        tier,
    )

    c_emoji = CHAIN_EMOJI.get(chain, "")
    link    = tx_link(tx_hash, chain) if tx_hash else ""

    from_str = f"`{short_addr(from_addr)}`"
    if from_label:
        from_str += f" **({from_label})**"

    to_str = f"`{short_addr(to_addr)}`"
    if to_label:
        to_str += f" **({to_label})**"

    color   = COLOR_BUY if direction == "BUY" else COLOR_SELL if direction == "SELL" else COLOR_INFO
    d_emoji = dir_emoji(direction)

    lines = [
        f"**{d_emoji} {direction}  ·  {symbol}  ·  {fmt_usd(amount_usd)}** {c_emoji}",
        f"📦 `{amount_token:,.4f} {symbol}`",
        f"👤 From: {from_str}",
        f"📬 To:   {to_str}",
    ]

    # Optional cluster context
    if data.get("cluster_label"):
        conf = data.get("cluster_confidence", 0.0)
        lines.append(f"🕵️ Cluster: **{data['cluster_label']}**  ·  {conf * 100:.0f}% confidence")

    # Optional cross-chain entity context
    also_active = data.get("cross_chain_also_active")
    if also_active:
        badges = " · ".join(
            f"{CHAIN_EMOJI.get(c, '')} {c.capitalize()}" for c in also_active
        )
        entity_name = data.get("cross_chain_entity", "")
        lines.append(f"🌐 **{entity_name}** also active on {badges}")

    if link:
        lines.append(f"🔗 [View transaction ↗]({link})  ·  Block `{block}`")
    lines.append(_branding_line())

    title  = f"🐋 Whale Alert  ·  {chain_badge(chain)}"
    footer = _score_footer(score)
    return title, lines, color, footer


def _format_accumulation_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from an accumulation alert dict."""
    wallet = data.get("wallet_address", "")
    symbol = data.get("token_symbol") or "Unknown"
    chain  = data.get("chain", "ethereum")
    buys   = data.get("buy_count", 0)
    total  = data.get("total_usd", 0.0)
    avg    = data.get("avg_per_tx_usd", 0.0)
    window = data.get("window_hours", 24)
    label  = data.get("wallet_label")

    wallet_str = f"`{short_addr(wallet)}`"
    if label:
        wallet_str += f" **({label})**"

    c_emoji = CHAIN_EMOJI.get(chain, "")
    lines = [
        f"**🔁 {symbol}  ·  {buys}× buys in {window}h** {c_emoji}",
        f"👤 Wallet: {wallet_str}",
        f"💰 Total:  **{fmt_usd(total)}**  ·  Avg/tx: {fmt_usd(avg)}",
        f"📡 Repeated accumulation pattern detected",
        _branding_line(),
    ]
    title  = f"📈 Accumulation Signal  ·  {chain_badge(chain)}"
    footer = f"Repeated buy pattern over {window}h window"
    return title, lines, COLOR_BUY, footer


def _format_price_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a price alert dict."""
    token     = data.get("token_symbol") or data.get("token_id", "unknown")
    price     = data.get("current_price_usd") or data.get("current_price", 0.0)
    target    = data.get("target_price_usd") or data.get("target_price", 0.0)
    condition = data.get("condition", "above")
    pct_change = data.get("pct_change_24h", 0.0)

    emoji          = "🚀" if condition == "above" else "📉"
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


def _format_exchange_flow_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from an exchange flow alert dict."""
    chain       = data.get("chain", "ethereum")
    direction   = data.get("flow_direction", "OUTFLOW")
    exchange    = data.get("exchange_name", "Exchange")
    symbol      = data.get("token_symbol", "???")
    amount_usd  = data.get("amount_usd", 0.0)
    amount_tok  = data.get("amount_token", 0.0)
    wallet_addr = data.get("wallet_address", "")
    wallet_lbl  = data.get("wallet_label")
    tx_hash     = data.get("tx_hash", "")
    score       = data.get("priority_score", 0)

    c_emoji    = CHAIN_EMOJI.get(chain, "")
    link       = tx_link(tx_hash, chain) if tx_hash else ""
    wallet_str = f"`{short_addr(wallet_addr)}`"
    if wallet_lbl:
        wallet_str += f" **({wallet_lbl})**"

    if direction == "OUTFLOW":
        signal_emoji = "🔴"
        signal_label = "SELL SIGNAL"
        action       = f"moved **{fmt_usd(amount_tok)} {symbol}** ➜ **{exchange}**"
        color        = COLOR_SELL
    else:
        signal_emoji = "🟢"
        signal_label = "BUY SIGNAL"
        action       = f"received **{fmt_usd(amount_tok)} {symbol}** from **{exchange}**"
        color        = COLOR_BUY

    lines = [
        f"**{signal_emoji} {signal_label}  ·  {symbol}  ·  {fmt_usd(amount_usd)}** {c_emoji}",
        f"👤 {wallet_str} {action}",
    ]
    if link:
        lines.append(f"🔗 [View transaction ↗]({link})")
    lines.append(_branding_line())

    title  = f"{signal_emoji} Exchange Flow  ·  {chain_badge(chain)}"
    footer = _score_footer(score)
    return title, lines, color, footer


def _format_daily_summary_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a daily summary alert dict."""
    date_label   = data.get("date_label", "")
    alert_count  = data.get("alert_count_24h", 0)
    total_vol    = data.get("total_volume_24h_usd", 0.0)
    top_moves    = data.get("top_moves") or []
    most_token   = data.get("most_accumulated_token")
    most_vol     = data.get("most_accumulated_volume", 0.0)
    flow_dir     = data.get("top_flow_direction")
    flow_token   = data.get("top_flow_token")
    flow_usd     = data.get("top_flow_amount_usd", 0.0)

    lines = [
        f"**📊 {alert_count} whale moves detected  ·  Total volume: {fmt_usd(total_vol)}**",
    ]

    if most_token:
        lines.append(f"🔁 Most accumulated: **{most_token}** ({fmt_usd(most_vol)} buy volume)")

    if flow_dir and flow_token:
        flow_emoji = "🔴" if flow_dir == "OUTFLOW" else "🟢"
        flow_label = "being sent to exchanges" if flow_dir == "OUTFLOW" else "flowing from exchanges to whales"
        lines.append(f"{flow_emoji} Top exchange flow: **{flow_token}** {fmt_usd(flow_usd)} — {flow_label}")

    if top_moves:
        lines.append("**🐋 Top 5 moves:**")
        for i, move in enumerate(top_moves[:5], start=1):
            d_emoji = {"BUY": "🟢", "SELL": "🔴", "SEND": "🔵"}.get(move.get("direction", "SEND"), "⚪")
            c_emoji = CHAIN_EMOJI.get(move.get("chain", ""), "")
            lines.append(
                f"`#{i}` {d_emoji} **{move.get('token', 'native')}**  ·  "
                f"{fmt_usd(move.get('amount_usd', 0))} {c_emoji}"
            )

    lines.append(_branding_line())

    title  = f"📅 Daily Whale Summary  ·  {date_label}"
    footer = "Smart Money Tracker — Daily digest at 08:00 UTC"
    return title, lines, COLOR_INFO, footer


def _format_cluster_alert(data: dict) -> tuple[str, list[str], discord.Color, str]:
    """Build CV2 title, lines, color, footer from a wallet cluster alert dict."""
    chain        = data.get("chain", "ethereum")
    cluster_id   = data.get("cluster_id", "?")
    label        = data.get("cluster_label") or f"Cluster #{cluster_id}"
    confidence   = data.get("confidence", 0.0)
    member_count = data.get("member_count", 2)
    methods_csv  = data.get("detection_methods", "")
    volume       = data.get("total_volume_usd", 0.0)
    addresses    = data.get("member_addresses", [])
    labels_list  = data.get("member_labels", [])
    score        = data.get("priority_score", 0)

    _METHOD_EMOJI = {"funding": "💸", "timing": "⏱️", "pattern": "🔁"}
    methods_display = " · ".join(
        f"{_METHOD_EMOJI.get(m.strip(), '🔍')} {m.strip().capitalize()}"
        for m in methods_csv.split(",") if m.strip()
    ) or "Unknown"

    # ASCII confidence bar (8 blocks)
    conf_filled = round(confidence * 8)
    conf_bar    = "█" * conf_filled + "░" * (8 - conf_filled)

    c_emoji = CHAIN_EMOJI.get(chain, "")

    # Up to 3 member addresses shown inline
    member_parts: list[str] = []
    for i, addr in enumerate(addresses[:3]):
        lbl = labels_list[i] if i < len(labels_list) and labels_list[i] else short_addr(addr)
        member_parts.append(f"`{lbl}`")
    if member_count > 3:
        member_parts.append(f"*+{member_count - 3} more*")

    lines = [
        f"**🕵️ {label}**  ·  {member_count} coordinated wallets {c_emoji}",
        f"💰 Combined volume: **{fmt_usd(volume)}**",
        f"🎯 Confidence: `{conf_bar}` **{confidence * 100:.0f}%**",
        f"📡 Detection: {methods_display}",
        f"👥 Wallets: {' · '.join(member_parts)}",
        _branding_line(),
    ]
    title  = f"🕵️ Wallet Cluster  ·  {chain_badge(chain)}"
    footer = _score_footer(score)
    return title, lines, COLOR_WARN, footer


# ── Channel config matching ───────────────────────────────────────────────────

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


# ── Main push loop ────────────────────────────────────────────────────────────

async def _push_loop(bot: commands.Bot) -> None:
    """Main loop: connect to WebSocket, receive alerts, post to channels."""
    backoff = 1
    ws_url  = settings.ws_url.rstrip("/") + "/ws/alerts"

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
        logger.warning("Auto-push: no active channels configured — alert dropped")
        return

    # Format the alert based on type
    alert_type = data.get("alert_type", "whale")
    logger.info(
        "Auto-push: received alert_type=%s score=%s",
        alert_type, data.get("priority_score"),
    )

    # Whale alerts are tier-aware (free = masked Pro labels, pro = full labels).
    # All other types use a single fallback view sent to every channel.
    free_view     = None
    pro_view      = None
    fallback_view = None

    if alert_type == "whale":
        free_title, free_lines, free_color, free_footer = _format_whale_alert(data, tier="free")
        pro_title,  pro_lines,  pro_color,  pro_footer  = _format_whale_alert(data, tier="pro")
        free_view = build_cv2(title=free_title, lines=free_lines, color=free_color, footer=free_footer)
        pro_view  = build_cv2(title=pro_title,  lines=pro_lines,  color=pro_color,  footer=pro_footer)
    elif alert_type == "price":
        title, lines, color, footer = _format_price_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    elif alert_type == "accumulation":
        title, lines, color, footer = _format_accumulation_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    elif alert_type == "exchange_flow":
        title, lines, color, footer = _format_exchange_flow_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    elif alert_type == "wallet_cluster":
        title, lines, color, footer = _format_cluster_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    elif alert_type == "daily_summary":
        title, lines, color, footer = _format_daily_summary_alert(data)
        fallback_view = build_cv2(title=title, lines=lines, color=color, footer=footer)
    else:
        # portfolio alerts not pushed to Discord channels
        logger.debug("Auto-push: alert_type=%s not pushed to channels", alert_type)
        return

    for cfg in channels:
        if not _matches_channel_config(data, cfg):
            logger.debug(
                "Auto-push: alert_type=%s filtered out for channel %s "
                "(min_score=%s alert_types=%s chains=%s)",
                alert_type, cfg.get("channel_id"),
                cfg.get("min_score"), cfg.get("alert_types"), cfg.get("chains"),
            )
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
