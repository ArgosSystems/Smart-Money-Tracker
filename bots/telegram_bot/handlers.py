"""
bots/telegram_bot/handlers.py
------------------------------
Telegram command handlers — full parity with the Discord bot.

Commands
--------
/start                                 – welcome + command list
/track  <address> [label]              – track a wallet
/untrack <address>                     – stop tracking
/alerts [count]                        – recent whale alerts
/smartmoney <token>                    – whale activity for token
/trending                              – top tokens whales are buying
/status                                – API health check
/wallet_pnl <address> [chain]          – realized + unrealized P&L
/accumulation_alerts [chain] [count]   – accumulation pattern alerts
/set_alerts [min_score] [chains] [types] – enable alert push for this chat
/who_is <address> [chain]              – Smart Label database lookup
/scan_token <mint>                     – Solana token safety check
/twitter_status                        – Twitter broadcaster status (admin only)

Formatting uses Telegram HTML parse_mode to match the Discord CV2 style.
"""

from __future__ import annotations

import logging
import os

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import settings

logger = logging.getLogger(__name__)

# Resolved from settings.api_url — set API_BASE_URL in .env for external hosts.
API_BASE = settings.api_url.rstrip("/") + "/api/v1"

# Supported chain names (mirrors CHAIN_CHOICES in Discord _shared.py)
VALID_CHAINS = {
    "ethereum", "base", "arbitrum", "bsc", "polygon", "optimism", "solana"
}

CHAIN_EMOJI: dict[str, str] = {
    "ethereum": "⬛",
    "base":     "🔵",
    "arbitrum": "🔶",
    "bsc":      "🟡",
    "polygon":  "🟣",
    "optimism": "🔴",
    "solana":   "◎",
}


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
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.1f}K"
    return f"{sign}${a:.2f}"


def signed_usd(v: float) -> str:
    """Like fmt_usd but always shows + prefix for positive values."""
    s = fmt_usd(v)
    return f"+{s}" if v >= 0 else s


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 10 else addr


def dir_emoji(d: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "SEND": "🔵"}.get(d.upper(), "⚪")


def chain_badge(chain: str) -> str:
    emoji = CHAIN_EMOJI.get(chain, "🔗")
    return f"{emoji} {chain.capitalize()}"


def tg_box(title: str, lines: list[str], footer: str | None = None) -> str:
    """Format a structured message block (mirrors Discord CV2 container style)."""
    sep = "─" * 28
    body = "\n".join(lines)
    msg = f"<b>{title}</b>\n{sep}\n{body}"
    if footer:
        msg += f"\n{sep}\n<i>{footer}</i>"
    return msg


def _is_telegram_admin(user_id: int) -> bool:
    """Check against TELEGRAM_ADMIN_IDS env var (comma-separated int IDs)."""
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
    if not raw:
        return False
    ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    return user_id in ids


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = tg_box(
        "🐋 Smart Money Tracker",
        [
            "Track on-chain whale wallets in real time across 6 chains.\n",
            "<b>Whale tracking</b>",
            "/track &lt;address&gt; [label] — track a wallet",
            "/untrack &lt;address&gt; — stop tracking",
            "/alerts [count] — recent whale moves",
            "/smartmoney &lt;token&gt; — activity for a token",
            "/trending — top tokens whales are buying",
            "",
            "<b>P&amp;L &amp; Accumulation</b>",
            "/wallet_pnl &lt;address&gt; [chain] — realized + unrealized P&amp;L",
            "/accumulation_alerts [chain] [count] — repeated buys detected",
            "",
            "<b>Utilities</b>",
            "/who_is &lt;address&gt; [chain] — Smart Label lookup",
            "/scan_token &lt;mint&gt; — Solana rug-check",
            "/set_alerts [min_score] [chains] [types] — push alerts to this chat",
            "/status — API health",
            "/twitter_status — Twitter broadcaster (admin only)",
            "/bot_stats — signal win rate and avg gains",
            "",
            "<b>Clustering</b>",
            "/clusters [chain] [count] — entities behind multiple wallets",
            "/wallet_cluster &lt;address&gt; [chain] — which cluster owns this wallet",
        ],
        footer="Powered by Smart Money Tracker",
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /track &lt;address&gt; [label]", parse_mode="HTML"
        )
        return

    address = args[0]
    label   = " ".join(args[1:]) if len(args) > 1 else None

    payload: dict = {"address": address}
    if label:
        payload["label"] = label

    data = await _post("/wallets/track", payload)
    if data is None:
        await update.message.reply_text(
            "❌ Failed to track wallet. Check the address format.", parse_mode="HTML"
        )
        return

    lines = [f"Address: <code>{data['address']}</code>"]
    if data.get("label"):
        lines.append(f"Label: <b>{data['label']}</b>")
    lines.append(f"Chain: {chain_badge(data.get('chain', 'ethereum'))}")
    await update.message.reply_text(
        tg_box("✅ Wallet Tracked", lines), parse_mode="HTML"
    )


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /untrack &lt;address&gt;", parse_mode="HTML"
        )
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

    lines: list[str] = []
    for alert in data:
        emoji  = dir_emoji(alert["direction"])
        symbol = alert.get("token_symbol") or "ETH"
        usd    = fmt_usd(alert["amount_usd"])
        chain  = alert.get("chain", "ethereum")
        lines.append(
            f"{emoji} <b>{alert['direction']}</b> {symbol} • {usd} {CHAIN_EMOJI.get(chain, '')}\n"
            f"  <code>{short(alert['from_address'])}</code> → <code>{short(alert['to_address'])}</code>\n"
            f"  Tx: <code>{alert['tx_hash'][:14]}…</code>"
        )

    await update.message.reply_text(
        tg_box(f"🐋 Recent Whale Alerts ({len(data)})", lines),
        parse_mode="HTML",
    )


async def cmd_smartmoney(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /smartmoney &lt;symbol or address&gt;", parse_mode="HTML"
        )
        return

    token = args[0]
    data  = await _get(f"/alerts/token/{token}", params={"limit": 20})

    if not data:
        await update.message.reply_text(
            f"No whale activity found for <b>{token.upper()}</b>.", parse_mode="HTML"
        )
        return

    buys     = [a for a in data if a["direction"] == "BUY"]
    sells    = [a for a in data if a["direction"] == "SELL"]
    buy_vol  = sum(a["amount_usd"] for a in buys)
    sell_vol = sum(a["amount_usd"] for a in sells)
    symbol   = data[0].get("token_symbol") or token.upper()
    sentiment = "📈 Accumulating" if buy_vol >= sell_vol else "📉 Distributing"

    lines = [
        f"🟢 Buys: {len(buys)} txs • {fmt_usd(buy_vol)}",
        f"🔴 Sells: {len(sells)} txs • {fmt_usd(sell_vol)}",
        f"Sentiment: {sentiment}",
        "",
        "<b>Recent transactions:</b>",
    ]
    for alert in data[:5]:
        lines.append(
            f"{dir_emoji(alert['direction'])} {alert['direction']} {fmt_usd(alert['amount_usd'])}\n"
            f"  <code>{short(alert['from_address'])}</code> → <code>{short(alert['to_address'])}</code>"
        )

    await update.message.reply_text(
        tg_box(f"🐋 Smart Money — {symbol}", lines), parse_mode="HTML"
    )


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await _get("/tokens/trending", params={"limit": 10})
    if not data:
        await update.message.reply_text("No trending data yet.")
        return

    lines: list[str] = []
    for i, token in enumerate(data, start=1):
        chain = token.get("chain", "ethereum")
        lines.append(
            f"<b>#{i} {token['token_symbol']}</b> {CHAIN_EMOJI.get(chain, '')}\n"
            f"  🟢 {token['buy_count']} buys  🔴 {token['sell_count']} sells\n"
            f"  Volume: {fmt_usd(token['total_volume_usd'])}"
        )

    await update.message.reply_text(
        tg_box("🔥 Trending Tokens (Whale Buys)", lines), parse_mode="HTML"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(settings.api_url.rstrip("/") + "/health")
            resp.raise_for_status()
            h = resp.json()
        lines = [
            f"Whale threshold: <b>${h['whale_threshold_usd']:,.0f}</b>",
            f"Poll interval: {h['poll_interval_seconds']}s",
        ]
        await update.message.reply_text(
            tg_box("✅ API Online", lines), parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(
            "❌ API is offline. Run <code>python start.py</code> first.",
            parse_mode="HTML",
        )


# ── New commands (Task 2 parity) ──────────────────────────────────────────────

async def cmd_wallet_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /wallet_pnl <address> [chain]
    Show realized + unrealized P&L for all tracked tokens in a wallet.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /wallet_pnl &lt;address&gt; [chain]", parse_mode="HTML"
        )
        return

    address = args[0]
    chain   = args[1].lower() if len(args) > 1 else None

    if chain and chain not in VALID_CHAINS:
        await update.message.reply_text(
            f"Unknown chain <b>{chain}</b>. Valid: {', '.join(sorted(VALID_CHAINS))}",
            parse_mode="HTML",
        )
        return

    params: dict = {}
    if chain:
        params["chain"] = chain

    data = await _get(f"/wallets/{address}/pnl", params=params)
    if data is None:
        await update.message.reply_text("❌ Could not retrieve P&L data. Check address format.")
        return

    if not isinstance(data, list) or not data:
        await update.message.reply_text(
            tg_box(
                "No P&L data yet",
                [
                    f"<code>{address}</code> has no recorded positions.",
                    "P&L tracking starts automatically once whale alerts are detected.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    total_realized   = sum(pos["realized_pnl_usd"]         for pos in data)
    total_unrealized = sum(pos.get("unrealized_pnl_usd", 0.0) for pos in data)
    total_pnl        = total_realized + total_unrealized
    chain_label      = chain_badge(chain) if chain else "All Chains"

    lines: list[str] = [
        f"Wallet: <code>{short(address)}</code>",
        f"Realized P&amp;L: <b>{signed_usd(total_realized)}</b>",
        f"Unrealized P&amp;L: <b>{signed_usd(total_unrealized)}</b>",
        f"Total P&amp;L: <b>{signed_usd(total_pnl)}</b>",
        "",
    ]

    for pos in data[:10]:
        cname      = pos.get("chain", "ethereum")
        symbol     = pos.get("token_symbol") or pos.get("token_key", "?")
        realized   = pos.get("realized_pnl_usd", 0.0)
        unrealized = pos.get("unrealized_pnl_usd", 0.0)
        total      = realized + unrealized
        bought     = pos.get("total_bought_usd", 0.0)
        sold       = pos.get("total_sold_usd", 0.0)
        txs        = pos.get("tx_count", 0)
        avg        = pos.get("avg_cost_usd", 0.0)
        pnl_emoji  = "📈" if total >= 0 else "📉"

        lines.append(
            f"{pnl_emoji} <b>{symbol}</b> {CHAIN_EMOJI.get(cname, '')} — {signed_usd(total)}\n"
            f"  Realized: {signed_usd(realized)}  |  Unrealized: {signed_usd(unrealized)}\n"
            f"  Bought: {fmt_usd(bought)}  |  Sold: {fmt_usd(sold)}  |  Avg: ${avg:,.4f}  |  Txs: {txs}"
        )

    await update.message.reply_text(
        tg_box(
            f"Wallet P&amp;L — {chain_label}",
            lines,
            footer=f"{len(data)} position(s) — Smart Money Tracker",
        ),
        parse_mode="HTML",
    )


async def cmd_accumulation_alerts(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /accumulation_alerts [chain] [count]
    Show wallets repeatedly accumulating the same token.
    """
    args  = context.args or []
    chain = None
    count = 10

    for arg in args:
        if arg.lower() in VALID_CHAINS:
            chain = arg.lower()
        elif arg.isdigit():
            count = max(1, min(int(arg), 20))

    params: dict = {"limit": count}
    if chain:
        params["chain"] = chain

    data = await _get("/alerts/accumulations", params=params)
    if not isinstance(data, list):
        await update.message.reply_text("❌ API error or no accumulation data.")
        return

    if not data:
        await update.message.reply_text(
            tg_box(
                "No accumulation patterns detected",
                [
                    "No wallet has triggered the accumulation pattern yet.",
                    "Pattern fires when a wallet buys the same token ≥ 3 times in 24 h with ≥ $50K volume.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    chain_label = chain_badge(chain) if chain else "All Chains"
    lines: list[str] = []

    for event in data:
        cname  = event.get("chain", "ethereum")
        symbol = event.get("token_symbol") or "Unknown"
        wallet = event.get("wallet_address", "")
        buys   = event.get("buy_count", 0)
        total  = event.get("total_usd", 0.0)
        avg    = event.get("avg_per_tx_usd", 0.0)
        window = event.get("window_hours", 24)
        fired  = (event.get("fired_at") or "")[:16].replace("T", " ")

        lines.append(
            f"🔁 <b>{symbol}</b> {CHAIN_EMOJI.get(cname, '')}\n"
            f"  Wallet: <code>{short(wallet)}</code>\n"
            f"  {buys} buys in {window}h — Total: {fmt_usd(total)} — Avg: {fmt_usd(avg)}/tx\n"
            f"  Detected: {fired} UTC"
        )

    await update.message.reply_text(
        tg_box(
            f"Accumulation Alerts — {chain_label}",
            lines,
            footer="Smart Money Tracker — Accumulation Detection",
        ),
        parse_mode="HTML",
    )


async def cmd_clusters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /clusters [chain] [count]
    List detected wallet clusters — groups of wallets belonging to the same entity.
    """
    args  = context.args or []
    chain = None
    count = 8

    for arg in args:
        if arg.lower() in VALID_CHAINS:
            chain = arg.lower()
        elif arg.isdigit():
            count = max(1, min(int(arg), 10))

    params: dict = {"limit": count, "min_confidence": 0.6}
    if chain:
        params["chain"] = chain

    data = await _get("/clusters", params=params)
    if not isinstance(data, list):
        await update.message.reply_text("❌ API error or no cluster data.")
        return

    if not data:
        chain_label = chain_badge(chain) if chain else "All Chains"
        await update.message.reply_text(
            tg_box(
                f"No clusters detected — {chain_label}",
                [
                    "No wallet clusters found with confidence ≥ 60%.",
                    "",
                    "<b>Detection methods:</b>",
                    "💸 Funding — wallet A sent ETH to B; B trades same tokens",
                    "⏱️ Timing — wallets bought same token within 5 min, 3+ times",
                    "🔁 Pattern — 3+ wallets, same direction, same token, same hour, 2+ times",
                    "",
                    "Clusters appear after enough whale activity is recorded.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    chain_label = chain_badge(chain) if chain else "All Chains"
    lines: list[str] = []

    for cluster in data:
        cluster_id   = cluster.get("id", "?")
        cname        = cluster.get("chain", "ethereum")
        label        = cluster.get("cluster_label") or f"Cluster #{cluster_id}"
        methods_csv  = cluster.get("detection_methods", "")
        confidence   = float(cluster.get("confidence", 0.0))
        member_count = cluster.get("member_count", 0)
        volume       = float(cluster.get("total_volume_usd", 0.0))
        members      = cluster.get("members", [])

        methods_str = " · ".join(
            {"funding": "💸 Funding", "timing": "⏱️ Timing", "pattern": "🔁 Pattern"}.get(m.strip(), m)
            for m in methods_csv.split(",") if m.strip()
        )
        member_addrs = " | ".join(
            short(m["wallet_address"])
            + (f" ({m['wallet_label']})" if m.get("wallet_label") else "")
            for m in members[:3]
        )
        more = f" +{member_count - 3} more" if member_count > 3 else ""

        lines.append(
            f"🕵️ <b>{label}</b> {CHAIN_EMOJI.get(cname, '')} — {confidence * 100:.0f}% confidence\n"
            f"  Wallets ({member_count}): {member_addrs}{more}\n"
            f"  Volume: {fmt_usd(volume)}  |  Methods: {methods_str}"
        )

    await update.message.reply_text(
        tg_box(
            f"Wallet Clusters — {chain_label}",
            lines,
            footer="Smart Money Tracker — Whale Clustering (10-min refresh)",
        ),
        parse_mode="HTML",
    )


async def cmd_wallet_cluster(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /wallet_cluster <address> [chain]
    Check which cluster (entity) a wallet address belongs to.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /wallet_cluster &lt;address&gt; [chain]", parse_mode="HTML"
        )
        return

    address = args[0]
    chain   = args[1].lower() if len(args) > 1 else None

    if chain and chain not in VALID_CHAINS:
        await update.message.reply_text(
            f"Unknown chain <b>{chain}</b>. Valid: {', '.join(sorted(VALID_CHAINS))}",
            parse_mode="HTML",
        )
        return

    params: dict = {}
    if chain:
        params["chain"] = chain

    data = await _get(f"/clusters/wallet/{address}", params=params)
    if not isinstance(data, list):
        await update.message.reply_text("❌ API error looking up wallet cluster.")
        return

    if not data:
        chain_label = chain_badge(chain) if chain else "any chain"
        await update.message.reply_text(
            tg_box(
                "Wallet not in any cluster",
                [
                    f"<code>{address}</code> is not part of any detected cluster on {chain_label}.",
                    "",
                    "This means no coordination signals were found with other tracked wallets.",
                    "Clustering runs every 10 minutes — check back after more activity is recorded.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    lines: list[str] = []

    for cluster in data:
        cluster_id   = cluster.get("id", "?")
        cname        = cluster.get("chain", "ethereum")
        label        = cluster.get("cluster_label") or f"Cluster #{cluster_id}"
        methods_csv  = cluster.get("detection_methods", "")
        confidence   = float(cluster.get("confidence", 0.0))
        member_count = cluster.get("member_count", 0)
        volume       = float(cluster.get("total_volume_usd", 0.0))
        members      = cluster.get("members", [])

        # Find how this wallet was linked
        my_sigs = methods_csv
        for m in members:
            if m.get("wallet_address", "").lower() == address.lower():
                my_sigs = m.get("detection_signals", methods_csv)
                break

        sig_str = " · ".join(
            {"funding": "💸 Funding", "timing": "⏱️ Timing", "pattern": "🔁 Pattern"}.get(s.strip(), s)
            for s in my_sigs.split(",") if s.strip()
        )

        # Other members (exclude queried wallet)
        others = [
            m for m in members
            if m.get("wallet_address", "").lower() != address.lower()
        ]
        others_str = " | ".join(
            short(m["wallet_address"])
            + (f" ({m['wallet_label']})" if m.get("wallet_label") else "")
            for m in others[:4]
        )
        if member_count - 1 > 4:
            others_str += f" +{member_count - 5} more"

        lines.append(
            f"🕵️ <b>{label}</b> {CHAIN_EMOJI.get(cname, '')} — {confidence * 100:.0f}% confidence\n"
            f"  Linked by: {sig_str}\n"
            f"  Co-wallets: {others_str or '—'}\n"
            f"  Combined volume: {fmt_usd(volume)}\n"
            f"  Use /clusters for full list"
        )

    chain_label = chain_badge(chain) if chain else "All Chains"
    await update.message.reply_text(
        tg_box(
            f"Cluster Membership — {short(address)}",
            lines,
            footer="Smart Money Tracker — Wallet Clustering",
        ),
        parse_mode="HTML",
    )


async def cmd_exchange_flows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /exchange_flows [chain] [OUTFLOW|INFLOW] [count]
    Show recent whale exchange flow events.
    OUTFLOW = whale sent tokens TO an exchange (sell 🔴)
    INFLOW  = whale received tokens FROM an exchange (buy 🟢)
    """
    args      = context.args or []
    chain     = None
    direction = None
    count     = 10

    for arg in args:
        if arg.lower() in VALID_CHAINS:
            chain = arg.lower()
        elif arg.upper() in ("OUTFLOW", "INFLOW"):
            direction = arg.upper()
        elif arg.isdigit():
            count = max(1, min(int(arg), 15))

    params: dict = {"limit": count}
    if chain:
        params["chain"] = chain
    if direction:
        params["direction"] = direction

    data = await _get("/exchange-flows", params=params)
    if not isinstance(data, list):
        await update.message.reply_text("❌ API error or no exchange flow data.")
        return

    if not data:
        dir_label   = f" ({direction})" if direction else ""
        chain_label = chain_badge(chain) if chain else "All Chains"
        await update.message.reply_text(
            tg_box(
                f"No exchange flow events — {chain_label}{dir_label}",
                [
                    "No exchange flow events detected yet.",
                    "Events fire when a tracked whale sends to or receives from a known exchange.",
                    "Add exchange addresses to SmartLabel with entity_type = 'exchange'.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    chain_label = chain_badge(chain) if chain else "All Chains"
    lines: list[str] = []

    for event in data:
        cname      = event.get("chain", "ethereum")
        direction_ = event.get("flow_direction", "OUTFLOW")
        exch_name  = event.get("exchange_name", "Unknown Exchange")
        wallet     = event.get("wallet_address", "")
        symbol     = event.get("token_symbol") or "native"
        amount_usd = event.get("amount_usd", 0.0)
        amount_tok = event.get("amount_token", 0.0)
        fired      = (event.get("fired_at") or "")[:16].replace("T", " ")

        signal_emoji = "🔴" if direction_ == "OUTFLOW" else "🟢"
        action_label = f"→ {exch_name} (SELL)" if direction_ == "OUTFLOW" else f"← {exch_name} (BUY)"

        lines.append(
            f"{signal_emoji} <b>{symbol}</b> {CHAIN_EMOJI.get(cname, '')}\n"
            f"  Wallet: <code>{short(wallet)}</code> {action_label}\n"
            f"  {fmt_usd(amount_usd)}  ·  {amount_tok:,.4f} {symbol}\n"
            f"  Detected: {fired} UTC"
        )

    dir_label = f" — {direction}" if direction else ""
    await update.message.reply_text(
        tg_box(
            f"Exchange Flow Alerts — {chain_label}{dir_label}",
            lines,
            footer="Smart Money Tracker — Exchange Flow Detection",
        ),
        parse_mode="HTML",
    )


async def cmd_set_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /set_alerts [min_score] [chains] [alert_types]
    Enable real-time whale/price alert push in this Telegram chat.
    Uses chat_id as the identifier (mirrors Discord set_alert_channel).

    Examples:
      /set_alerts
      /set_alerts 50
      /set_alerts 30 ethereum,bsc
      /set_alerts 0 all whale,price
    """
    args        = context.args or []
    chat_id_str = str(update.effective_chat.id)
    min_score   = 0.0
    chains_str  = None
    types_str   = None

    # Parse positional args: [min_score] [chains] [types]
    if len(args) >= 1 and args[0].replace(".", "").isdigit():
        min_score = float(args[0])
    if len(args) >= 2 and args[1].lower() != "all":
        chains_str = args[1]
    if len(args) >= 3 and args[2].lower() != "all":
        types_str = args[2]

    payload: dict = {
        "guild_id":    chat_id_str,
        "channel_id":  chat_id_str,
        "min_score":   min_score,
    }
    if chains_str:
        payload["chains"] = chains_str
    if types_str:
        payload["alert_types"] = types_str

    data = await _post("/alert-channels", payload)
    if data is None:
        await update.message.reply_text(
            "❌ Failed to configure alerts. This chat may already be registered — "
            "use /set_alerts again to update.",
            parse_mode="HTML",
        )
        return

    lines = [
        f"Chat ID: <code>{chat_id_str}</code>",
        f"Min Score: <b>{data.get('min_score', 0.0)}</b>",
        f"Chains: <b>{data.get('chains') or 'All'}</b>",
        f"Alert Types: <b>{data.get('alert_types') or 'All'}</b>",
        f"Status: {'🟢 Active' if data.get('is_active') else '⚪ Inactive'}",
    ]
    await update.message.reply_text(
        tg_box(
            "✅ Alert Push Configured",
            lines,
            footer=f"Config ID: {data['id']}",
        ),
        parse_mode="HTML",
    )


async def cmd_who_is(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /who_is <address> [chain]
    Look up an address in the Smart Label database.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /who_is &lt;address&gt; [chain]", parse_mode="HTML"
        )
        return

    address     = args[0]
    chain       = args[1].lower() if len(args) > 1 else "ethereum"
    chat_id_str = str(update.effective_chat.id)

    data = await _get(f"/guilds/{chat_id_str}/whois/{address}", params={"chain": chain})
    if data is None:
        await update.message.reply_text("❌ Could not fetch label data.")
        return

    name        = data.get("name")
    entity_type = data.get("entity_type")
    is_masked   = data.get("is_masked")
    tier        = data.get("tier")

    if not name:
        await update.message.reply_text(
            tg_box(
                "Unknown Address",
                [
                    f"<code>{address}</code> is not in the Smart Label database.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    if is_masked:
        lines = [
            f"Name: <b>{name}</b>",
            "",
            "<i>This label is part of the Pro database. Upgrade to see full details.</i>",
        ]
        await update.message.reply_text(
            tg_box("🔍 Smart Label Lookup", lines), parse_mode="HTML"
        )
    else:
        lines = [
            f"Address: <code>{address}</code>",
            f"Entity: <b>{name}</b> ({entity_type})",
            f"Tier: {tier.capitalize() if tier else 'Unknown'}",
        ]
        await update.message.reply_text(
            tg_box("🔍 Smart Label Lookup", lines), parse_mode="HTML"
        )


_LEVEL_EMOJI = {"danger": "🔴", "warn": "🟡", "info": "🔵"}


async def cmd_scan_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /scan_token <mint>
    Solana token safety check — rug-pull detection, mint authority, LP lock & top holders.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /scan_token &lt;solana_mint_address&gt;", parse_mode="HTML"
        )
        return

    mint = args[0]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{API_BASE}/token-safety/{mint}")
            if resp.status_code == 200:
                data: dict | None = resp.json()
                err = ""
            else:
                try:
                    err = resp.json().get("detail", resp.text)
                except Exception:
                    err = resp.text[:300]
                data = None
    except httpx.ConnectError:
        data = None
        err = "Cannot reach the API — is <code>python start.py</code> running?"
    except Exception as exc:
        data = None
        err = str(exc)

    if data is None:
        await update.message.reply_text(
            tg_box("❌ Scan Failed", [err or "Could not fetch safety data."]),
            parse_mode="HTML",
        )
        return

    risk_level: str = data.get("risk_level", "UNKNOWN")
    score: int      = data.get("score", 0)
    rugged: bool    = data.get("rugged", False)
    name: str       = data.get("name") or "Unknown Token"
    symbol: str     = data.get("symbol") or "???"

    if rugged:
        verdict = "⛔ RUGGED"
    elif risk_level == "SAFE":
        verdict = "✅ SAFE"
    elif risk_level == "CAUTION":
        verdict = "⚠️ CAUTION"
    else:
        verdict = "🚨 DANGER"

    mint_auth   = "✅ Revoked" if data.get("mint_authority_revoked")   else "❌ Active (dev can print tokens)"
    freeze_auth = "✅ Revoked" if data.get("freeze_authority_revoked") else "❌ Active (dev can freeze wallets)"
    liquidity   = fmt_usd(data.get("total_liquidity_usd", 0))
    lp_locked   = f"{data.get('lp_locked_pct', 0):.1f}%"
    top1        = f"{data.get('top_holder_pct', 0):.1f}%"
    top5        = f"{data.get('top5_holders_pct', 0):.1f}%"

    lines = [
        f"Verdict: <b>{verdict}</b>  |  Risk Score: {score:,}",
        f"Mint Authority: {mint_auth}",
        f"Freeze Authority: {freeze_auth}",
        f"Total Liquidity: {liquidity}  |  LP Locked: {lp_locked}",
        f"Top Holder: {top1} of supply  |  Top 5: {top5} of supply",
    ]

    risks = data.get("risks") or []
    if risks:
        lines.append("")
        lines.append("<b>Risk Factors:</b>")
        for r in risks:
            emoji = _LEVEL_EMOJI.get(r.get("level", "info"), "⚪")
            desc  = r.get("description") or ""
            lines.append(
                f"{emoji} <b>{r['name']}</b> (+{r['score']})"
                + (f"\n  {desc}" if desc else "")
            )

    await update.message.reply_text(
        tg_box(
            f"◎ {name} ({symbol}) — Token Safety",
            lines,
            footer=f"Mint: {short(mint)} · Powered by RugCheck.xyz",
        ),
        parse_mode="HTML",
    )


async def cmd_bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /bot_stats
    Show win rate and avg P&L per signal type based on historical backtesting.
    """
    data = await _get("/backtest/stats")

    if not isinstance(data, dict):
        await update.message.reply_text(
            "❌ No accuracy data yet. Stats appear once signals have been backfilled."
        )
        return

    def _fmt_block(label: str, emoji: str, stats: dict) -> str:
        total   = stats.get("total_signals", 0)
        correct = stats.get("total_correct", 0)
        wr      = stats.get("win_rate_pct", 0.0)
        p72     = stats.get("avg_pnl_72h_pct", 0.0)
        p7d     = stats.get("avg_pnl_7d_pct", 0.0)
        if total == 0:
            return f"{emoji} <b>{label}</b>\n  No signals processed yet."
        sign_72 = "+" if p72 >= 0 else ""
        sign_7d = "+" if p7d >= 0 else ""
        return (
            f"{emoji} <b>{label}</b>\n"
            f"  Win rate: <b>{wr:.0f}%</b> ({correct}/{total} signals correct)\n"
            f"  Avg gain: <b>{sign_72}{p72:.1f}%</b> at 72h · <b>{sign_7d}{p7d:.1f}%</b> at 7d"
        )

    acc  = data.get("accumulation",  {})
    exfl = data.get("exchange_flow", {})
    generated = (data.get("generated_at") or "")[:16].replace("T", " ")

    lines = [
        _fmt_block("Accumulation Signals",  "🎯", acc),
        _fmt_block("Exchange Flow Signals",  "📡", exfl),
        "",
        "<b>How we measure accuracy</b>",
        "A signal is <i>correct</i> if the token price moved &gt;5% in the "
        "predicted direction within 72 hours of the alert firing.",
        "Price data: DeFiLlama historical API.",
    ]

    await update.message.reply_text(
        tg_box(
            "Smart Money Tracker — Accuracy Stats",
            lines,
            footer=f"Based on all signals since launch · Updated: {generated} UTC",
        ),
        parse_mode="HTML",
    )


async def cmd_twitter_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /twitter_status  (admin only — set TELEGRAM_ADMIN_IDS env var)
    Show Twitter broadcaster status: queue depth, rate limits, circuit breaker.
    """
    user_id = update.effective_user.id if update.effective_user else 0
    if not _is_telegram_admin(user_id):
        await update.message.reply_text(
            "⛔ This command is restricted to admins.\n"
            "Set <code>TELEGRAM_ADMIN_IDS=your_user_id</code> in <code>.env</code>.",
            parse_mode="HTML",
        )
        return

    data = await _get("/twitter/status")
    if data is None:
        await update.message.reply_text(
            "Twitter broadcasting is not enabled or the API is unreachable."
        )
        return

    mode        = data.get("mode", "unknown").upper()
    running     = "Yes" if data.get("running") else "No"
    queue_depth = data.get("queue_depth", 0)

    rl             = data.get("rate_limiter", {})
    remaining_day  = rl.get("remaining_today", "?")
    remaining_hour = rl.get("remaining_this_hour", "?")
    daily_budget   = rl.get("daily_budget", "?")
    hourly_cap     = rl.get("hourly_cap", "?")

    cb          = data.get("circuit_breaker", {})
    cb_state    = cb.get("state", "unknown").upper()
    cb_failures = cb.get("consecutive_failures", 0)

    features      = data.get("features", {})
    whale         = "On" if features.get("whale_tweets") else "Off"
    price         = "On" if features.get("price_tweets") else "Off"
    portfolio     = "On" if features.get("portfolio_tweets") else "Off"
    accumulation  = "On" if features.get("accumulation_tweets") else "Off"

    lines = [
        f"Mode: <b>{mode}</b>  |  Running: <b>{running}</b>",
        f"Queue Depth: <b>{queue_depth}</b> alerts pending",
        f"Budget: {remaining_day}/{daily_budget} today  |  {remaining_hour}/{hourly_cap} this hour",
        f"Circuit Breaker: <b>{cb_state}</b> ({cb_failures} consecutive failures)",
        f"Features: Whale={whale}  |  Price={price}  |  Portfolio={portfolio}  |  Accumulation={accumulation}",
    ]

    recent = await _get("/twitter/recent", params={"limit": "5"})
    if recent:
        lines.append("")
        lines.append("<b>Last 5 tweets (dry-run):</b>")
        for post in recent:
            status  = post.get("tweet_id") or "dry-run"
            content = (post.get("content") or "")[:60]
            score   = post.get("priority_score", 0)
            lines.append(f"• [{score:.0f}pts] {content}… ({status})")

    await update.message.reply_text(
        tg_box("🐦 Twitter Broadcaster Status", lines),
        parse_mode="HTML",
    )


# ── Register all handlers ─────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",                cmd_start))
    app.add_handler(CommandHandler("track",                cmd_track))
    app.add_handler(CommandHandler("untrack",              cmd_untrack))
    app.add_handler(CommandHandler("alerts",               cmd_alerts))
    app.add_handler(CommandHandler("smartmoney",           cmd_smartmoney))
    app.add_handler(CommandHandler("trending",             cmd_trending))
    app.add_handler(CommandHandler("status",               cmd_status))
    # Task 2 — new parity commands
    app.add_handler(CommandHandler("wallet_pnl",           cmd_wallet_pnl))
    app.add_handler(CommandHandler("accumulation_alerts",  cmd_accumulation_alerts))
    app.add_handler(CommandHandler("set_alerts",           cmd_set_alerts))
    app.add_handler(CommandHandler("who_is",               cmd_who_is))
    app.add_handler(CommandHandler("scan_token",           cmd_scan_token))
    app.add_handler(CommandHandler("twitter_status",       cmd_twitter_status))
    app.add_handler(CommandHandler("exchange_flows",       cmd_exchange_flows))
    app.add_handler(CommandHandler("clusters",             cmd_clusters))
    app.add_handler(CommandHandler("wallet_cluster",       cmd_wallet_cluster))
    app.add_handler(CommandHandler("bot_stats",            cmd_bot_stats))
