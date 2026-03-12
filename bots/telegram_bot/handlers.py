"""
bots/telegram_bot/handlers.py
------------------------------
Telegram command handlers — mirror of the Discord commands.

Commands
--------
/start                         – welcome message
/track  <address> [label]      – track a wallet
/untrack <address>             – stop tracking
/alerts [count]                – recent whale alerts
/smartmoney <token>            – whale activity for token
/trending                      – top tokens whales are buying
/status                        – API health check
"""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import settings

logger = logging.getLogger(__name__)

# Resolved from settings.api_url — set API_BASE_URL in .env for external hosts.
API_BASE = settings.api_url.rstrip("/") + "/api/v1"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def _get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("GET %s: %s", path, exc)
        return None


async def _post(path: str, payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{API_BASE}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("POST %s: %s", path, exc)
        return None


async def _delete(path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{API_BASE}{path}")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("DELETE %s: %s", path, exc)
        return None


# ── Format helpers ────────────────────────────────────────────────────────────

def fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.2f}"


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 10 else addr


def dir_emoji(d: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "SEND": "🔵"}.get(d.upper(), "⚪")


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🐋 *Smart Money Tracker*\n\n"
        "Track Ethereum whale wallets in real time.\n\n"
        "*Commands:*\n"
        "/track `<address>` \\[label\\] — track a wallet\n"
        "/untrack `<address>` — stop tracking\n"
        "/alerts \\[count\\] — recent whale moves\n"
        "/smartmoney `<token>` — activity for a token\n"
        "/trending — top tokens whales are buying\n"
        "/status — API health\n"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /track <address> [label]")
        return

    address = args[0]
    label   = " ".join(args[1:]) if len(args) > 1 else None

    payload: dict = {"address": address}
    if label:
        payload["label"] = label

    data = await _post("/wallets/track", payload)
    if data is None:
        await update.message.reply_text("❌ Failed to track wallet. Check the address format.")
        return

    msg = f"✅ Tracking `{data['address']}`"
    if data.get("label"):
        msg += f" — *{data['label']}*"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /untrack <address>")
        return

    data = await _delete(f"/wallets/{args[0]}")
    if data is None:
        await update.message.reply_text("❌ Wallet not found or API error.")
        return
    await update.message.reply_text(f"🗑️ {data.get('message', 'Removed.')}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args  = context.args or []
    count = int(args[0]) if args and args[0].isdigit() else 10
    count = max(1, min(count, 25))

    data = await _get("/alerts", params={"limit": count})
    if not data:
        await update.message.reply_text("No whale alerts yet.")
        return

    lines = [f"🐋 *Recent Whale Alerts ({len(data)})*\n"]
    for alert in data:
        emoji  = dir_emoji(alert["direction"])
        symbol = alert.get("token_symbol") or "ETH"
        usd    = fmt_usd(alert["amount_usd"])
        lines.append(
            f"{emoji} *{alert['direction']}* {symbol} • {usd}\n"
            f"  `{short(alert['from_address'])}` → `{short(alert['to_address'])}`\n"
            f"  Tx: `{alert['tx_hash'][:14]}…`\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_smartmoney(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /smartmoney <symbol or address>")
        return

    token = args[0]
    data  = await _get(f"/alerts/token/{token}", params={"limit": 20})

    if not data:
        await update.message.reply_text(f"No whale activity found for *{token.upper()}*.", parse_mode="Markdown")
        return

    buys       = [a for a in data if a["direction"] == "BUY"]
    sells      = [a for a in data if a["direction"] == "SELL"]
    buy_vol    = sum(a["amount_usd"] for a in buys)
    sell_vol   = sum(a["amount_usd"] for a in sells)
    symbol     = data[0].get("token_symbol") or token.upper()
    sentiment  = "📈 Accumulating" if buy_vol >= sell_vol else "📉 Distributing"

    msg = (
        f"🐋 *Smart Money — {symbol}*\n\n"
        f"🟢 Buys: {len(buys)} txs • {fmt_usd(buy_vol)}\n"
        f"🔴 Sells: {len(sells)} txs • {fmt_usd(sell_vol)}\n"
        f"Sentiment: {sentiment}\n\n"
        "*Recent transactions:*\n"
    )
    for alert in data[:5]:
        msg += (
            f"{dir_emoji(alert['direction'])} {alert['direction']} {fmt_usd(alert['amount_usd'])}\n"
            f"  `{short(alert['from_address'])}` → `{short(alert['to_address'])}`\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await _get("/tokens/trending", params={"limit": 10})
    if not data:
        await update.message.reply_text("No trending data yet.")
        return

    lines = ["🔥 *Trending Tokens (Whale Buys)*\n"]
    for i, token in enumerate(data, start=1):
        lines.append(
            f"*#{i} {token['token_symbol']}*\n"
            f"  🟢 {token['buy_count']} buys  🔴 {token['sell_count']} sells\n"
            f"  Volume: {fmt_usd(token['total_volume_usd'])}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(settings.api_url.rstrip("/") + "/health")
            resp.raise_for_status()
            h = resp.json()
        msg = (
            f"✅ *API Online*\n"
            f"Whale threshold: ${h['whale_threshold_usd']:,.0f}\n"
            f"Poll interval: {h['poll_interval_seconds']}s"
        )
    except Exception:
        msg = "❌ API is offline. Run `python start.py` first."

    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Register all handlers ─────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("track",      cmd_track))
    app.add_handler(CommandHandler("untrack",    cmd_untrack))
    app.add_handler(CommandHandler("alerts",     cmd_alerts))
    app.add_handler(CommandHandler("smartmoney", cmd_smartmoney))
    app.add_handler(CommandHandler("trending",   cmd_trending))
    app.add_handler(CommandHandler("status",     cmd_status))
