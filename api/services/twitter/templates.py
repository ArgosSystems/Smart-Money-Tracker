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


def _brand_sig() -> str:
    """
    One-line brand signature appended to every tweet.

    Includes abbreviated GitHub and Discord links (https:// stripped) when
    configured so the footer stays within the 280-char budget.
    """
    try:
        from config.settings import settings  # noqa: PLC0415
        parts: list[str] = [settings.brand_name]
        if settings.brand_github_url:
            gh = settings.brand_github_url.replace("https://", "").replace("http://", "").rstrip("/")
            parts.append(gh)
        if settings.brand_discord_invite:
            dc = settings.brand_discord_invite.replace("https://", "").replace("http://", "").rstrip("/")
            parts.append(dc)
        return "📡 " + "  ·  ".join(parts)
    except Exception:
        return "📡 Smart Money Tracker"


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
        return f"{addr[:6]}…{addr[-4:]}"
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


def _conf_bar(confidence: float, width: int = 6) -> str:
    """Compact ASCII confidence bar: ████░░ 85%"""
    filled = round(confidence * width)
    return "█" * filled + "░" * (width - filled)


# ── Template renderers ────────────────────────────────────────────────────────

class TweetRenderer:
    """
    Renders AlertDTO events into tweet-ready text strings.

    Respects the 280-character limit.  If content is too long, truncates
    the last line first (hashtags), then hard-truncates.
    """

    def render(self, event: AlertDTO, score: float) -> str:
        """Render a single alert into tweet text."""
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
        meta      = event.metadata
        chain     = event.chain.capitalize()
        ce        = chain_emoji(event.chain)
        de        = direction_emoji(meta.get("direction", "SEND"))
        amount    = fmt_number(meta.get("amount_token", 0))
        usd       = fmt_usd(meta.get("amount_usd", 0))
        symbol    = meta.get("token_symbol", "???")
        direction = meta.get("direction", "SEND")
        action    = {"BUY": "bought", "SELL": "sold", "SEND": "moved"}.get(direction, "moved")

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
        tx_url  = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""

        # Cluster context line (optional)
        cluster_line = ""
        if meta.get("cluster_label"):
            cluster_line = f"\n🕵️ Cluster: {meta['cluster_label']}"

        # Cross-chain entity context (optional)
        cross_chain_line = ""
        also_active = meta.get("cross_chain_also_active")
        if also_active:
            chains_str = ", ".join(c.capitalize() for c in also_active)
            cross_chain_line = f"\n🌐 {meta.get('cross_chain_entity', '')} also on {chains_str}"

        if score > 80:
            tweet = (
                f"🚨 WHALE ALERT  {ce} {chain}\n\n"
                f"{de} {direction}  ·  {symbol}  ·  {usd}\n"
                f"📦 {amount} {symbol}\n"
                f"👤 {from_label} ➜ {to_label}"
                f"{cluster_line}"
                f"{cross_chain_line}"
            )
        else:
            tweet = (
                f"🐋 Whale Alert  {ce} {chain}\n\n"
                f"{from_label} {action} {amount} {symbol} ({usd})\n"
                f"👤 ➜ {to_label}"
                f"{cluster_line}"
                f"{cross_chain_line}"
            )

        if tx_url:
            tweet += f"\n🔗 {tx_url}"

        tweet += f"\n\n{_brand_sig()}"
        tweet += f"\n#WhaleAlert #{chain} #OnChain #DeFi"
        return tweet

    def _render_price(self, event: AlertDTO, score: float) -> str:
        meta       = event.metadata
        symbol     = meta.get("token_symbol", "???")
        current    = meta.get("current_price_usd", 0.0)
        target     = meta.get("target_price_usd", 0.0)
        condition  = meta.get("condition", "above")
        pct_change = meta.get("pct_change_24h", 0.0) or 0.0
        chain      = event.chain.capitalize()
        ce         = chain_emoji(event.chain)

        direction_word = "broke above" if condition == "above" else "dropped below"
        rockets = _rocket_emojis(abs(pct_change)) if condition == "above" else "📉"

        tweet = (
            f"🎯 Price Alert  {ce} {chain}\n\n"
            f"{rockets} {symbol} {direction_word} ${target:,.4f}\n"
            f"💵 Now: ${current:,.4f}  ·  Target: ${target:,.4f}"
        )

        if pct_change:
            sign  = "+" if pct_change > 0 else ""
            tweet += f"\n📊 24h: {sign}{pct_change:.1f}%"

        label = meta.get("label")
        if label:
            tweet += f"\n📝 {label}"

        tweet += f"\n\n{_brand_sig()}"
        tweet += f"\n#{symbol} #{chain} #PriceAlert #Crypto"
        return tweet

    def _render_accumulation(self, event: AlertDTO, score: float) -> str:
        meta         = event.metadata
        wallet       = meta.get("wallet_address", "")
        wallet_label = meta.get("wallet_label") or short_addr(wallet)
        symbol       = meta.get("token_symbol", "???")
        buy_count    = meta.get("buy_count", 0)
        total_usd    = fmt_usd(meta.get("total_usd", 0.0))
        window_hours = meta.get("window_hours", 24)
        avg_usd      = fmt_usd(meta.get("avg_per_tx_usd", 0.0))
        chain        = event.chain.capitalize()
        ce           = chain_emoji(event.chain)

        tweet = (
            f"📈 Accumulation Signal  {ce} {chain}\n\n"
            f"🔁 {wallet_label} bought {symbol} {buy_count}× in {window_hours}h\n"
            f"💰 Total: {total_usd}  ·  Avg/tx: {avg_usd}\n\n"
            f"{_brand_sig()}\n"
            f"#{symbol} #{chain} #SmartMoney #OnChain"
        )
        return tweet

    def _render_cluster(self, event: AlertDTO, score: float) -> str:
        meta         = event.metadata
        cluster_id   = meta.get("cluster_id", "?")
        label        = meta.get("cluster_label") or f"Cluster #{cluster_id}"
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

        conf = f"{confidence * 100:.0f}%"

        tweet = (
            f"🕵️ Whale Cluster  {ce} {chain}\n\n"
            f"👥 {label} — {member_count} wallets\n"
            f"💰 Combined: {fmt_usd(volume)}\n"
            f"🎯 {conf} confidence  ·  {methods_str}\n\n"
            f"{_brand_sig()}\n"
            f"#WhaleCluster #{chain} #SmartMoney #DeFi"
        )
        return tweet

    def _render_exchange_flow(self, event: AlertDTO, score: float) -> str:
        meta      = event.metadata
        direction = meta.get("flow_direction", "OUTFLOW")
        exchange  = meta.get("exchange_name", "Exchange")
        symbol    = meta.get("token_symbol", "???")
        amount    = fmt_number(meta.get("amount_token", 0.0))
        usd       = fmt_usd(meta.get("amount_usd", 0.0))
        wallet    = meta.get("wallet_label") or short_addr(meta.get("wallet_address", ""))
        tx_hash   = meta.get("tx_hash", "")
        chain     = event.chain.capitalize()
        ce        = chain_emoji(event.chain)
        tx_url    = chain_explorer_url(tx_hash, event.chain) if tx_hash else ""

        if direction == "OUTFLOW":
            signal_emoji = "🔴"
            action       = f"moved {amount} {symbol} TO {exchange}"
            signal_label = "SELL SIGNAL"
        else:
            signal_emoji = "🟢"
            action       = f"received {amount} {symbol} FROM {exchange}"
            signal_label = "BUY SIGNAL"

        tweet = (
            f"{signal_emoji} Exchange Flow  {ce} {chain}\n\n"
            f"📡 {signal_label}\n"
            f"👤 {wallet} {action}\n"
            f"💰 {usd}"
        )

        if tx_url:
            tweet += f"\n🔗 {tx_url}"

        tweet += f"\n\n{_brand_sig()}"
        tweet += f"\n#{symbol} #{chain} #ExchangeFlow #SmartMoney"
        return tweet

    def _render_portfolio(self, event: AlertDTO, score: float) -> str:
        meta = event.metadata

        # Privacy guard: never expose identifying info unless is_public
        if not meta.get("is_public", False):
            return ""

        change_pct = meta.get("balance_change_pct", 0.0)
        change_usd = meta.get("balance_change_usd", 0.0)
        total_usd  = fmt_usd(meta.get("current_total_usd", 0.0))
        symbol     = meta.get("native_symbol", "")
        chain      = event.chain.capitalize()
        ce         = chain_emoji(event.chain)

        direction = "📈" if change_pct >= 0 else "📉"
        sign      = "+" if change_pct >= 0 else ""

        tweet = (
            f"{direction} Portfolio Update  {ce} {chain}\n\n"
            f"💼 {sign}{change_pct:.1f}%  ({sign}{fmt_usd(abs(change_usd))})\n"
            f"📊 Total: {total_usd} {symbol}\n\n"
            f"{_brand_sig()}\n"
            f"#{chain} #Portfolio #SmartMoney"
        )
        return tweet

    # ── Length enforcement ──────────────────────────────────────────────────────

    def _enforce_limit(self, text: str) -> str:
        """Truncate tweet to MAX_TWEET_LENGTH characters."""
        if len(text) <= MAX_TWEET_LENGTH:
            return text

        # Try removing the last line (usually hashtags)
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
