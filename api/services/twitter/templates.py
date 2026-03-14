"""
api/services/twitter/templates.py
----------------------------------
Tweet content rendering engine.

Lightweight template system — no Jinja2 dependency.  Uses Python string
formatting with helper functions for number abbreviation, address truncation,
and emoji selection.

Template tiers:
  CRITICAL (score > 80):  urgent emoji, large numbers, percentage of holdings
  STANDARD (score 30-80): concise tx summary with explorer link
  PRICE:                  milestone formatting with rockets for big gains
  PORTFOLIO:              privacy-sanitized — never expose wallet labels
"""

from __future__ import annotations

import logging
from datetime import datetime

from api.events.protocol import AlertDTO, AlertType

logger = logging.getLogger(__name__)

MAX_TWEET_LENGTH = 280


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_number(v: float) -> str:
    """Abbreviate large numbers: 1,234,567 → 1.2M, 500,000 → 500K."""
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:.2f}"


def fmt_usd(v: float) -> str:
    return f"${fmt_number(v)}"


def short_addr(addr: str) -> str:
    """Truncate address: keep first 6, last 4."""
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def direction_emoji(d: str) -> str:
    return {"BUY": "📥", "SELL": "📤", "SEND": "➡️"}.get(d.upper(), "🔄")


def chain_emoji(chain: str) -> str:
    return {
        "ethereum": "⬛", "base": "🔵", "arbitrum": "🔶",
        "bsc": "🟡", "polygon": "🟣", "optimism": "🔴", "solana": "◎",
    }.get(chain.lower(), "🔗")


def chain_explorer_url(tx_hash: str, chain: str) -> str:
    explorers = {
        "ethereum": "etherscan.io", "base": "basescan.org",
        "arbitrum": "arbiscan.io", "bsc": "bscscan.com",
        "polygon": "polygonscan.com", "optimism": "optimistic.etherscan.io",
        "solana": "solscan.io",
    }
    explorer = explorers.get(chain.lower(), "etherscan.io")
    clean_hash = tx_hash.split(":")[0] if ":" in tx_hash else tx_hash
    return f"https://{explorer}/tx/{clean_hash}"


def _rocket_emojis(pct: float) -> str:
    """Scale rocket emojis based on percentage gain."""
    if pct >= 100:
        return "🚀🚀🚀"
    if pct >= 50:
        return "🚀🚀"
    if pct >= 10:
        return "🚀"
    return "📈"


# ── Template renderers ────────────────────────────────────────────────────────

class TweetRenderer:
    """
    Renders AlertDTO events into tweet-ready text strings.

    Respects the 280-character limit.  If content is too long, truncates
    the explorer link first, then the description.
    """

    def render(self, event: AlertDTO, score: float) -> str:
        """Render a single alert into tweet text."""
        if event.alert_type == AlertType.WHALE:
            text = self._render_whale(event, score)
        elif event.alert_type == AlertType.PRICE:
            text = self._render_price(event, score)
        elif event.alert_type == AlertType.PORTFOLIO:
            text = self._render_portfolio(event, score)
        else:
            text = f"🔔 Alert on {event.chain}: ID #{event.alert_id}"

        return self._enforce_limit(text)

    def render_thread(self, events: list[AlertDTO], score: float) -> list[str]:
        """
        Compose a thread from multiple related alerts.

        First tweet: summary.
        Replies: individual alert details.
        """
        if not events:
            return []

        first = events[0]
        meta = first.metadata
        entity = (
            meta.get("from_label")
            or meta.get("to_label")
            or meta.get("token_symbol")
            or short_addr(meta.get("from_address", ""))
        )
        chain = first.chain.capitalize()

        header = (
            f"🧵 {entity} made {len(events)} moves on {chain} "
            f"in the last 10 minutes\n\n"
            f"Thread below 👇"
        )
        tweets = [self._enforce_limit(header)]

        for ev in events:
            tweets.append(self.render(ev, score))

        return tweets

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

        # Entity-first formatting: use label if available
        from_label = meta.get("from_label") or short_addr(meta.get("from_address", "???"))
        to_label = meta.get("to_label") or short_addr(meta.get("to_address", "???"))

        tx_hash = meta.get("tx_hash", "")
        tx_url = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""

        if score > 80:
            # Critical tier
            tweet = (
                f"🚨 WHALE ALERT\n\n"
                f"{de} {from_label} → {to_label}\n"
                f"💰 {amount} {symbol} ({usd})\n"
                f"{ce} {chain}\n"
            )
        else:
            # Standard tier
            action = {"BUY": "bought", "SELL": "sold", "SEND": "moved"}.get(direction, "transferred")
            tweet = (
                f"🐋 {from_label} {action} {amount} {symbol} ({usd}) "
                f"on {ce} {chain}\n"
            )

        if tx_url:
            tweet += f"\n🔗 {tx_url}"

        return tweet

    def _render_price(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata
        symbol = meta.get("token_symbol", "???")
        current = meta.get("current_price_usd", 0.0)
        target = meta.get("target_price_usd", 0.0)
        condition = meta.get("condition", "above")
        pct_change = meta.get("pct_change_24h", 0.0) or 0.0
        chain = event.chain.capitalize()
        ce = chain_emoji(event.chain)

        rockets = _rocket_emojis(abs(pct_change))
        direction_word = "above" if condition == "above" else "below"

        tweet = f"🎯 {symbol} hit ${current:,.4f} ({direction_word} ${target:,.4f}) on {ce} {chain}"

        if pct_change:
            sign = "+" if pct_change > 0 else ""
            tweet += f"\n{rockets} {sign}{pct_change:.1f}% in 24h"

        label = meta.get("label")
        if label:
            tweet += f"\n📝 {label}"

        return tweet

    def _render_portfolio(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata

        # Privacy guard: never expose identifying info unless is_public
        if not meta.get("is_public", False):
            return ""

        change_pct = meta.get("balance_change_pct", 0.0)
        change_usd = meta.get("balance_change_usd", 0.0)
        total_usd = fmt_usd(meta.get("current_total_usd", 0.0))
        symbol = meta.get("native_symbol", "")
        chain = event.chain.capitalize()

        direction = "📈" if change_pct >= 0 else "📉"
        sign = "+" if change_pct >= 0 else ""

        tweet = (
            f"{direction} Smart Money wallet update on {chain}\n\n"
            f"💰 {sign}{change_pct:.1f}% ({sign}{fmt_usd(abs(change_usd))})\n"
            f"📊 Total: {total_usd} {symbol}"
        )
        return tweet

    # ── Length enforcement ──────────────────────────────────────────────────────

    def _enforce_limit(self, text: str) -> str:
        """Truncate tweet to MAX_TWEET_LENGTH characters."""
        if len(text) <= MAX_TWEET_LENGTH:
            return text

        # Try removing the last line (usually the explorer link)
        lines = text.rstrip().split("\n")
        if len(lines) > 1:
            shortened = "\n".join(lines[:-1])
            if len(shortened) <= MAX_TWEET_LENGTH:
                return shortened

        # Hard truncate
        return text[: MAX_TWEET_LENGTH - 3] + "..."


class ThreadComposer:
    """
    Groups related alerts for thread composition.

    Rule: 5+ alerts about the same entity within 10 minutes → thread.
    Buffers events keyed by entity, flushes when threshold is reached
    or the 10-minute window expires.
    """

    THREAD_THRESHOLD = 5
    WINDOW_SECONDS = 600  # 10 minutes

    def __init__(self) -> None:
        self._buffers: dict[str, list[tuple[float, AlertDTO]]] = {}

    def add_to_buffer(self, entity_key: str, event: AlertDTO) -> None:
        """Add an event to the buffer for the given entity."""
        import time
        now = time.monotonic()
        if entity_key not in self._buffers:
            self._buffers[entity_key] = []

        # Evict old entries outside the window
        self._buffers[entity_key] = [
            (t, e) for t, e in self._buffers[entity_key]
            if (now - t) < self.WINDOW_SECONDS
        ]
        self._buffers[entity_key].append((now, event))

    def should_thread(self, entity_key: str) -> bool:
        """Return True if this entity has enough buffered alerts for a thread."""
        buf = self._buffers.get(entity_key, [])
        return len(buf) >= self.THREAD_THRESHOLD

    def flush_thread(self, entity_key: str) -> list[AlertDTO]:
        """Return and clear all buffered events for this entity."""
        buf = self._buffers.pop(entity_key, [])
        return [event for _, event in buf]
