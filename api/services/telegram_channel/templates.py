"""
api/services/telegram_channel/templates.py
--------------------------------------------
Message rendering for Telegram Channel — HTML parse_mode, ≤4096 chars.

Reuses all formatting helpers from twitter/templates.py so number
formatting and chain metadata stay consistent across all broadcasters.
Adds HTML structure (bold, code, links) not possible on Twitter.
"""

from __future__ import annotations

import logging

from api.events.protocol import AlertDTO, AlertType
from api.services.twitter.templates import (
    _brand_sig,
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


class TelegramMessageRenderer:
    """
    Renders AlertDTO events into Telegram HTML-formatted messages.

    Compared to TweetRenderer:
      - 4096 char limit (vs 280) — full details without truncation
      - HTML bold/code/links for better readability
      - Clickable transaction links
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
        else:
            text = f"🔔 Alert on {event.chain}: ID #{event.alert_id}"
        return self._enforce_limit(text)

    # ── Per-type renderers ─────────────────────────────────────────────────────

    def _render_whale(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)
        de = direction_emoji(meta.get("direction", "SEND"))
        amount = fmt_number(meta.get("amount_token", 0))
        usd = fmt_usd(meta.get("amount_usd", 0))
        symbol = _html(meta.get("token_symbol", "???"))
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
        tx_url = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""
        badge = "🚨" if score > 80 else "🐋"

        msg = (
            f"{badge} <b>Whale Alert</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"{de} <b>{direction}</b>  ·  <b>{symbol}</b>  ·  <b>{usd}</b>\n"
            f"📦 {amount} {symbol}\n"
            f"👤 <code>{from_label}</code> → <code>{to_label}</code>"
        )
        if tx_url:
            msg += f'\n🔗 <a href="{tx_url}">View Transaction</a>'
        msg += f"\n\n<i>{_html(_brand_sig())}</i>"
        return msg

    def _render_price(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        symbol = _html(meta.get("token_symbol", "???"))
        current = meta.get("current_price_usd", 0.0)
        target = meta.get("target_price_usd", 0.0)
        condition = meta.get("condition", "above")
        pct_change = meta.get("pct_change_24h", 0.0) or 0.0
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)
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
            msg += f"\n📊 24h: <b>{sign}{pct_change:.1f}%</b>"
        label = meta.get("label")
        if label:
            msg += f"\n📝 {_html(label)}"
        msg += f"\n\n<i>{_html(_brand_sig())}</i>"
        return msg

    def _render_accumulation(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        wallet = meta.get("wallet_address", "")
        wallet_label = _html(meta.get("wallet_label") or short_addr(wallet))
        symbol = _html(meta.get("token_symbol", "???"))
        buy_count = meta.get("buy_count", 0)
        total_usd = fmt_usd(meta.get("total_usd", 0.0))
        window_hours = meta.get("window_hours", 24)
        avg_usd = fmt_usd(meta.get("avg_per_tx_usd", 0.0))
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)

        msg = (
            f"🔁 <b>Accumulation Alert</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"👤 <code>{wallet_label}</code>\n"
            f"Bought <b>{symbol}</b> <b>{buy_count}×</b> in {window_hours}h\n"
            f"💰 Total: <b>{total_usd}</b>  ·  Avg/tx: <b>{avg_usd}</b>\n\n"
            f"<i>{_html(_brand_sig())}</i>"
        )
        return msg

    def _render_portfolio(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        if not meta.get("is_public", False):
            return ""
        change_pct = meta.get("balance_change_pct", 0.0)
        change_usd = meta.get("balance_change_usd", 0.0)
        total_usd = fmt_usd(meta.get("current_total_usd", 0.0))
        symbol = _html(meta.get("native_symbol", ""))
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)
        direction = "📈" if change_pct >= 0 else "📉"
        sign = "+" if change_pct >= 0 else ""

        msg = (
            f"{direction} <b>Portfolio Update</b>  {ce} <b>{chain}</b>\n"
            f"{_SEP}\n"
            f"💼 <b>{sign}{change_pct:.1f}%</b>  ({sign}{fmt_usd(abs(change_usd))})\n"
            f"📊 Total: <b>{total_usd}</b> {symbol}\n\n"
            f"<i>{_html(_brand_sig())}</i>"
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
