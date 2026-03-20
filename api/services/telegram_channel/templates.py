"""
api/services/telegram_channel/templates.py
--------------------------------------------
Message rendering for Telegram Channel — HTML parse_mode, ≤4096 chars.

Reuses formatting helpers from twitter/templates.py so number
formatting and chain metadata stay consistent across all broadcasters.
Adds HTML structure (bold, code, links) not possible on Twitter.

Footer pattern on every card:
  ────────────────────────────
  🤖 BotName  ·  GitHub ↗  ·  Discord ↗
"""

from __future__ import annotations

import logging

from api.events.protocol import AlertDTO, AlertType
from api.services.twitter.templates import (
    _rocket_emojis,
    chain_emoji,
    chain_explorer_url,
    direction_emoji,
    fmt_number,
    fmt_usd,
    short_addr,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096  # Telegram's hard cap per message

_SEP = "─" * 28


def _html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse_mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tg_footer() -> str:
    """
    Rich HTML footer with brand name + clickable GitHub and Discord links.
    Shown at the bottom of every alert card separated by _SEP.
    """
    try:
        from config.settings import settings  # noqa: PLC0415
        parts: list[str] = [f"🤖 <b>{_html(settings.brand_name)}</b>"]
        if settings.brand_github_url:
            parts.append(f'<a href="{settings.brand_github_url}">GitHub ↗</a>')
        if settings.brand_discord_invite:
            parts.append(f'<a href="{settings.brand_discord_invite}">Discord ↗</a>')
        return " · ".join(parts)
    except Exception:
        return "🤖 <b>Smart Money Tracker</b>"


class TelegramMessageRenderer:
    """
    Renders AlertDTO events into Telegram HTML-formatted messages.

    Compared to TweetRenderer:
      - 4096 char limit (vs 280) — full details without truncation
      - HTML bold/code/links for better readability
      - Clickable transaction links
      - Rich footer with GitHub + Discord links
    """

    def render(self, event: AlertDTO, score: float) -> str:
        """Render a single alert into Telegram HTML text."""
        if event.alert_type == AlertType.WHALE:
            text = self._render_whale(event, score)
        elif event.alert_type == AlertType.PRICE:
            text = self._render_price(event, score)
        elif event.alert_type == AlertType.PORTFOLIO:
            text = self._render_portfolio(event, score)
        elif event.alert_type == AlertType.ACCUMULATION:
            text = self._render_accumulation(event, score)
        elif event.alert_type == AlertType.EXCHANGE_FLOW:
            text = self._render_exchange_flow(event, score)
        elif event.alert_type == AlertType.WALLET_CLUSTER:
            text = self._render_cluster(event, score)
        else:
            text = f"🔔 Alert on {event.chain}: ID #{event.alert_id}"
        return self._enforce_limit(text)

    # ── Per-type renderers ─────────────────────────────────────────────────────

    def _render_whale(self, event: AlertDTO, score: float) -> str:
        meta      = event.metadata
        chain     = event.chain.capitalize()
        ce        = chain_emoji(event.chain)
        de        = direction_emoji(meta.get("direction", "SEND"))
        amount    = fmt_number(meta.get("amount_token", 0))
        usd       = fmt_usd(meta.get("amount_usd", 0))
        symbol    = _html(meta.get("token_symbol", "???"))
        direction = meta.get("direction", "SEND")

        from_label = _html(
            meta.get("from_smart_label_name")
            or meta.get("from_label")
            or short_addr(meta.get("from_address", "???"))
        )
        to_label = _html(
            meta.get("to_smart_label_name")
            or meta.get("to_label")
            or short_addr(meta.get("to_address", "???"))
        )
        tx_hash = meta.get("tx_hash", "")
        tx_url  = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""
        badge   = "🚨" if score > 80 else "🐋"

        # Optional cluster context
        cluster_line = ""
        if meta.get("cluster_label"):
            conf = meta.get("cluster_confidence", 0.0)
            cluster_line = (
                f"\n🕵️ Cluster: <b>{_html(meta['cluster_label'])}</b>"
                f"  ·  <i>{conf * 100:.0f}% confidence</i>"
            )

        # Optional cross-chain entity context
        cross_chain_line = ""
        also_active = meta.get("cross_chain_also_active")
        if also_active:
            chains_str = " · ".join(
                f"{chain_emoji(c)} {c.capitalize()}" for c in also_active
            )
            entity = _html(meta.get("cross_chain_entity", ""))
            cross_chain_line = f"\n🌐 <b>{entity}</b> also active on {chains_str}"

        msg = (
            f"{badge} <b>Whale Alert</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"{de} <b>{direction}</b>  ·  <b>{symbol}</b>  ·  <b>{usd}</b>\n"
            f"📦 <code>{amount} {symbol}</code>\n"
            f"👤 <code>{from_label}</code> ➜ <code>{to_label}</code>"
            f"{cluster_line}"
            f"{cross_chain_line}"
        )
        if tx_url:
            msg += f'\n🔗 <a href="{tx_url}">View Transaction ↗</a>'
        msg += f"\n{_SEP}\n{_tg_footer()}"
        return msg

    def _render_price(self, event: AlertDTO, score: float) -> str:
        meta       = event.metadata
        symbol     = _html(meta.get("token_symbol", "???"))
        current    = meta.get("current_price_usd", 0.0)
        target     = meta.get("target_price_usd", 0.0)
        condition  = meta.get("condition", "above")
        pct_change = meta.get("pct_change_24h", 0.0) or 0.0
        chain      = event.chain.capitalize()
        ce         = chain_emoji(event.chain)

        direction_word = "broke above" if condition == "above" else "dropped below"
        rockets = _rocket_emojis(abs(pct_change)) if condition == "above" else "📉"

        msg = (
            f"🎯 <b>Price Alert</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"{rockets} <b>{symbol}</b> {direction_word} <b>${target:,.4f}</b>\n"
            f"💵 Now: <code>${current:,.4f}</code>  ·  Target: <code>${target:,.4f}</code>"
        )
        if pct_change:
            sign = "+" if pct_change > 0 else ""
            msg += f"\n📊 24h change: <b>{sign}{pct_change:.1f}%</b>"
        label = meta.get("label")
        if label:
            msg += f"\n📝 <i>{_html(label)}</i>"
        msg += f"\n{_SEP}\n{_tg_footer()}"
        return msg

    def _render_accumulation(self, event: AlertDTO, score: float) -> str:
        meta         = event.metadata
        wallet       = meta.get("wallet_address", "")
        wallet_label = _html(meta.get("wallet_label") or short_addr(wallet))
        symbol       = _html(meta.get("token_symbol", "???"))
        buy_count    = meta.get("buy_count", 0)
        total_usd    = fmt_usd(meta.get("total_usd", 0.0))
        window_hours = meta.get("window_hours", 24)
        avg_usd      = fmt_usd(meta.get("avg_per_tx_usd", 0.0))
        chain        = event.chain.capitalize()
        ce           = chain_emoji(event.chain)

        msg = (
            f"📈 <b>Accumulation Signal</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"🔁 <b>{symbol}</b>  ·  <b>{buy_count}×</b> buys in {window_hours}h\n"
            f"👤 Wallet: <code>{wallet_label}</code>\n"
            f"💰 Total: <b>{total_usd}</b>  ·  Avg/tx: <b>{avg_usd}</b>\n"
            f"📡 Repeated accumulation pattern detected\n"
            f"{_SEP}\n{_tg_footer()}"
        )
        return msg

    def _render_cluster(self, event: AlertDTO, score: float) -> str:
        meta         = event.metadata
        cluster_id   = meta.get("cluster_id", "?")
        label        = _html(meta.get("cluster_label") or f"Cluster #{cluster_id}")
        member_count = meta.get("member_count", 2)
        confidence   = meta.get("confidence", 0.0)
        methods_csv  = meta.get("detection_methods", "")
        volume       = meta.get("total_volume_usd", 0.0)
        chain        = event.chain.capitalize()
        ce           = chain_emoji(event.chain)

        _METHOD_LABELS = {"funding": "💸 Funding", "timing": "⏱ Timing", "pattern": "🔁 Pattern"}
        methods_str = " · ".join(
            _METHOD_LABELS.get(m.strip(), m.strip())
            for m in methods_csv.split(",") if m.strip()
        ) or "Unknown"

        # Confidence bar
        conf_filled = round(confidence * 8)
        conf_bar    = "█" * conf_filled + "░" * (8 - conf_filled)

        member_addresses = meta.get("member_addresses", [])
        member_labels    = meta.get("member_labels", [])
        member_lines: list[str] = []
        for i, addr in enumerate(member_addresses[:5]):
            lbl = (member_labels[i] if i < len(member_labels) and member_labels[i]
                   else short_addr(addr))
            member_lines.append(f"  • <code>{_html(lbl)}</code>")
        if member_count > 5:
            member_lines.append(f"  • <i>+{member_count - 5} more wallets</i>")
        members_block = "\n".join(member_lines) if member_lines else "  —"

        msg = (
            f"🕵️ <b>Whale Cluster Detected</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"👥 <b>{label}</b>  —  {member_count} coordinated wallets\n"
            f"💰 Combined volume: <b>{fmt_usd(volume)}</b>\n"
            f"🎯 Confidence: <code>{conf_bar}</code> <b>{confidence * 100:.0f}%</b>\n"
            f"📡 Detection: {methods_str}\n"
            f"👤 Members:\n{members_block}\n"
            f"{_SEP}\n{_tg_footer()}"
        )
        return msg

    def _render_exchange_flow(self, event: AlertDTO, score: float) -> str:
        meta      = event.metadata
        direction = meta.get("flow_direction", "OUTFLOW")
        exchange  = _html(meta.get("exchange_name", "Exchange"))
        symbol    = _html(meta.get("token_symbol", "???"))
        amount    = fmt_number(meta.get("amount_token", 0.0))
        usd       = fmt_usd(meta.get("amount_usd", 0.0))
        wallet    = _html(meta.get("wallet_label") or short_addr(meta.get("wallet_address", "")))
        tx_hash   = meta.get("tx_hash", "")
        tx_url    = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""
        chain     = event.chain.capitalize()
        ce        = chain_emoji(event.chain)

        if direction == "OUTFLOW":
            signal_emoji = "🔴"
            signal_label = "SELL SIGNAL"
            action       = f"moved <b>{amount} {symbol}</b> TO <b>{exchange}</b>"
        else:
            signal_emoji = "🟢"
            signal_label = "BUY SIGNAL"
            action       = f"received <b>{amount} {symbol}</b> FROM <b>{exchange}</b>"

        msg = (
            f"{signal_emoji} <b>Exchange Flow</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"📡 <b>{signal_label}</b>\n"
            f"👤 <code>{wallet}</code> {action}\n"
            f"💰 Value: <b>{usd}</b>"
        )
        if tx_url:
            msg += f'\n🔗 <a href="{tx_url}">View Transaction ↗</a>'
        msg += f"\n{_SEP}\n{_tg_footer()}"
        return msg

    def _render_portfolio(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        if not meta.get("is_public", False):
            return ""
        change_pct = meta.get("balance_change_pct", 0.0)
        change_usd = meta.get("balance_change_usd", 0.0)
        total_usd  = fmt_usd(meta.get("current_total_usd", 0.0))
        symbol     = _html(meta.get("native_symbol", ""))
        chain      = event.chain.capitalize()
        ce         = chain_emoji(event.chain)
        direction  = "📈" if change_pct >= 0 else "📉"
        sign       = "+" if change_pct >= 0 else ""

        msg = (
            f"{direction} <b>Portfolio Update</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"💼 <b>{sign}{change_pct:.1f}%</b>  ({sign}{fmt_usd(abs(change_usd))})\n"
            f"📊 Total: <b>{total_usd}</b> {symbol}\n"
            f"{_SEP}\n{_tg_footer()}"
        )
        return msg

    # ── Length enforcement ──────────────────────────────────────────────────────

    def _enforce_limit(self, text: str) -> str:
        """Trim to Telegram's 4096-char hard cap."""
        if len(text) <= MAX_MESSAGE_LENGTH:
            return text
        lines = text.rstrip().split("\n")
        if len(lines) > 1:
            shortened = "\n".join(lines[:-1])
            if len(shortened) <= MAX_MESSAGE_LENGTH:
                return shortened
        return text[: MAX_MESSAGE_LENGTH - 3] + "..."
