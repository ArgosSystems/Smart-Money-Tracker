"""
api/services/bluesky/templates.py
-----------------------------------
Post content rendering for Bluesky — plain text, ≤300 graphemes.

Bluesky's character limit is 300 graphemes (not bytes).  We stay safe
by treating each Python `str` character as one grapheme, which holds for
all Western text and emoji.

Reuses formatting helpers from twitter/templates.py for consistency.
No HTML — Bluesky uses plain text with optional facets (links/mentions)
via the AT Protocol.  We keep it simple: plain text only.
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

MAX_POST_LENGTH = 300  # Bluesky's hard cap (graphemes)


class BlueSkyPostRenderer:
    """
    Renders AlertDTO events into Bluesky-ready plain text.

    Tighter limit than Twitter (300 vs 280) and no HTML, so content is
    more condensed than TelegramMessageRenderer but fuller than TweetRenderer.
    """

    def render(self, event: AlertDTO, score: float) -> str:
        """Render a single alert into Bluesky post text."""
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
        symbol = meta.get("token_symbol", "???")
        direction = meta.get("direction", "SEND")
        from_label = (
            meta.get("from_smart_label_name")
            or meta.get("from_label")
            or short_addr(meta.get("from_address", "???"))
        )
        to_label = (
            meta.get("to_smart_label_name")
            or meta.get("to_label")
            or short_addr(meta.get("to_address", "???"))
        )
        tx_hash = meta.get("tx_hash", "")
        tx_url = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""
        badge = "🚨" if score > 80 else "🐋"
        action = {"BUY": "bought", "SELL": "sold", "SEND": "moved"}.get(direction, "transferred")

        post = (
            f"{badge} Whale Alert {ce} {chain}\n\n"
            f"{de} {direction} · {symbol} · {usd}\n"
            f"📦 {amount} {symbol}\n"
            f"👤 {from_label} {action} → {to_label}"
        )
        if tx_url:
            post += f"\n🔗 {tx_url}"
        post += f"\n\n{_brand_sig()}"
        post += f"\n#WhaleAlert #{chain} #OnChain"
        return post

    def _render_price(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        symbol = meta.get("token_symbol", "???")
        current = meta.get("current_price_usd", 0.0)
        target = meta.get("target_price_usd", 0.0)
        condition = meta.get("condition", "above")
        pct_change = meta.get("pct_change_24h", 0.0) or 0.0
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)
        direction_word = "broke above" if condition == "above" else "dropped below"
        rockets = _rocket_emojis(abs(pct_change)) if condition == "above" else "📉"

        post = (
            f"🎯 Price Alert {ce} {chain}\n\n"
            f"{rockets} {symbol} {direction_word} ${target:,.4f}\n"
            f"💵 Now: ${current:,.4f} · Target: ${target:,.4f}"
        )
        if pct_change:
            sign = "+" if pct_change > 0 else ""
            post += f"\n📊 24h: {sign}{pct_change:.1f}%"
        label = meta.get("label")
        if label:
            post += f"\n📝 {label}"
        post += f"\n\n{_brand_sig()}\n#{symbol} #PriceAlert"
        return post

    def _render_accumulation(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        wallet = meta.get("wallet_address", "")
        wallet_label = meta.get("wallet_label") or short_addr(wallet)
        symbol = meta.get("token_symbol", "???")
        buy_count = meta.get("buy_count", 0)
        total_usd = fmt_usd(meta.get("total_usd", 0.0))
        window_hours = meta.get("window_hours", 24)
        avg_usd = fmt_usd(meta.get("avg_per_tx_usd", 0.0))
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)

        post = (
            f"🔁 Accumulation Alert {ce} {chain}\n\n"
            f"👤 {wallet_label} bought {symbol} {buy_count}× in {window_hours}h\n"
            f"💰 Total: {total_usd} · Avg/tx: {avg_usd}\n\n"
            f"{_brand_sig()}\n#{symbol} #SmartMoney #OnChain"
        )
        return post

    def _render_exchange_flow(self, event: AlertDTO, score: float) -> str:
        meta      = event.metadata
        direction = meta.get("flow_direction", "OUTFLOW")
        exchange  = meta.get("exchange_name", "Exchange")
        symbol    = meta.get("token_symbol", "???")
        amount    = fmt_number(meta.get("amount_token", 0.0))
        usd       = fmt_usd(meta.get("amount_usd", 0.0))
        wallet    = meta.get("wallet_label") or short_addr(meta.get("wallet_address", ""))
        tx_hash   = meta.get("tx_hash", "")
        tx_url    = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""
        chain     = event.chain.capitalize()
        ce        = chain_emoji(event.chain)

        if direction == "OUTFLOW":
            signal_emoji = "🔴"
            action       = f"moved {amount} {symbol} TO {exchange}"
            signal_label = "SELL SIGNAL"
        else:
            signal_emoji = "🟢"
            action       = f"received {amount} {symbol} FROM {exchange}"
            signal_label = "BUY SIGNAL"

        post = (
            f"{signal_emoji} Exchange Flow {ce} {chain}\n\n"
            f"📡 {signal_label}\n"
            f"👤 {wallet} {action}\n"
            f"💰 {usd}"
        )
        if tx_url:
            post += f"\n🔗 {tx_url}"
        post += f"\n\n{_brand_sig()}\n#{symbol} #WhaleAlert #SmartMoney"
        return post

    def _render_portfolio(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        if not meta.get("is_public", False):
            return ""
        change_pct = meta.get("balance_change_pct", 0.0)
        change_usd = meta.get("balance_change_usd", 0.0)
        total_usd = fmt_usd(meta.get("current_total_usd", 0.0))
        symbol = meta.get("native_symbol", "")
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)
        direction = "📈" if change_pct >= 0 else "📉"
        sign = "+" if change_pct >= 0 else ""

        post = (
            f"{direction} Portfolio Update {ce} {chain}\n\n"
            f"💼 {sign}{change_pct:.1f}% ({sign}{fmt_usd(abs(change_usd))})\n"
            f"📊 Total: {total_usd} {symbol}\n\n"
            f"{_brand_sig()}\n#{chain} #Portfolio #DeFi"
        )
        return post

    # ── Length enforcement ──────────────────────────────────────────────────────

    def _enforce_limit(self, text: str) -> str:
        """Truncate to Bluesky's 300-grapheme cap."""
        if len(text) <= MAX_POST_LENGTH:
            return text

        # Try removing the last line (usually hashtags or explorer URL)
        lines = text.rstrip().split("\n")
        if len(lines) > 1:
            shortened = "\n".join(lines[:-1])
            if len(shortened) <= MAX_POST_LENGTH:
                return shortened

        # Hard truncate
        return text[: MAX_POST_LENGTH - 3] + "..."
