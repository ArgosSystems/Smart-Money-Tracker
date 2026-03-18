"""
bots/discord_bot/cmd_pnl.py
-----------------------------
Wallet P&L slash commands:

  /wallet_pnl  <address> [chain]   — show realized + unrealized P&L for all tracked tokens
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_INFO, COLOR_SELL,
    api_get,
    chain_badge, cv2_error, short_addr,
)

# CV2 types aliased in _shared but not re-exported — pull from discord directly
_LayoutView: Any = discord.ui.LayoutView       # type: ignore[attr-defined]
_Container: Any  = discord.ui.Container        # type: ignore[attr-defined]
_TextDisplay: Any = discord.ui.TextDisplay     # type: ignore[attr-defined]
_Separator: Any  = discord.ui.Separator        # type: ignore[attr-defined]
_SeparatorSpacing: Any = discord.SeparatorSpacing  # type: ignore[attr-defined]


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Signed USD: +$1.2M / -$34.5K / +$0.56"""
    sign = "+" if v >= 0 else "-"
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.1f}K"
    return f"{sign}${a:,.2f}"


def _fmt_abs(v: float) -> str:
    """Unsigned USD: $1.2M / $34.5K / $0.56"""
    a = abs(v)
    if a >= 1_000_000:
        return f"${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"${a / 1_000:.1f}K"
    return f"${a:,.2f}"


def _fmt_price(v: float) -> str:
    """Format avg cost — keeps significant decimals for micro-cap tokens."""
    if v >= 1_000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.4f}"
    if v >= 0.0001:
        return f"${v:.6f}"
    return f"${v:.10f}"


def _summary_box(realized: float, unrealized: float, total: float) -> str:
    """Build a box-drawing P&L summary block (plain text — emoji render fine in Discord)."""
    r_icon = "💚" if realized >= 0 else "❤️"
    t_icon = "📈" if total    >= 0 else "📉"

    r = _fmt(realized)
    u = _fmt(unrealized)
    t = _fmt(total)

    return (
        "┌─ Summary ─────────────────────────┐\n"
        f"│  {r_icon} Realized:    {r}\n"
        f"│  📊 Unrealized:  {u}\n"
        "│  ─────────────────────────────────\n"
        f"│  {t_icon} Total P&L:   **{t}**\n"
        "└───────────────────────────────────┘"
    )


def _chain_section(chain_name: str, positions: list[dict]) -> str:
    """Format one chain block: totals row + per-token positions."""
    total_bought   = sum(p["total_bought_usd"] for p in positions)
    total_sold     = sum(p["total_sold_usd"]   for p in positions)
    total_txs      = sum(p["tx_count"]         for p in positions)
    chain_realized   = sum(p["realized_pnl_usd"]          for p in positions)
    chain_unrealized = sum(p.get("unrealized_pnl_usd", 0) for p in positions)
    chain_total      = chain_realized + chain_unrealized

    emoji = CHAIN_EMOJI.get(chain_name, "🔗")
    name  = chain_name.capitalize()
    pnl_arrow = "📈" if chain_total >= 0 else "📉"

    rows = [
        f"{emoji} **{name}**  {pnl_arrow} {_fmt(chain_total)}",
        f"Bought {_fmt_abs(total_bought)}  |  Sold {_fmt_abs(total_sold)}  |  Txs {total_txs}",
    ]

    # Top positions by absolute P&L impact (max 5)
    ranked = sorted(
        positions,
        key=lambda p: abs(p["realized_pnl_usd"] + p.get("unrealized_pnl_usd", 0)),
        reverse=True,
    )[:5]

    for pos in ranked:
        symbol = pos.get("token_symbol") or pos.get("token_key", "?")
        pnl    = pos["realized_pnl_usd"] + pos.get("unrealized_pnl_usd", 0.0)
        avg    = pos.get("avg_cost_usd", 0.0)
        txs    = pos["tx_count"]
        icon   = "▲" if pnl >= 0 else "▼"
        rows.append(
            f"  {icon} **{symbol}**: {_fmt(pnl)}"
            f"  |  Avg {_fmt_price(avg)}"
            f"  |  {txs} txs"
        )

    return "\n".join(rows)


async def _wallet_label(address: str) -> str | None:
    """Fetch the human label for a wallet from /wallets (returns None if not found)."""
    wallets = await api_get("/wallets", params={"active_only": "false"})
    if not isinstance(wallets, list):
        return None
    addr_lower = address.lower()
    for w in wallets:
        if w.get("address", "").lower() == addr_lower and w.get("label"):
            return w["label"]
    return None


# ── Command setup ─────────────────────────────────────────────────────────────

def setup_pnl(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="wallet_pnl",
        description="Show realized + unrealized P&L for all tracked tokens in a wallet",
    )
    @app_commands.describe(
        address="Wallet address (0x...)",
        chain="Filter by chain (leave blank for all)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def wallet_pnl(
        interaction: discord.Interaction,
        address: str,
        chain: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        params: dict = {}
        if chain:
            params["chain"] = chain.value

        # Parallel fetch — P&L data + wallet label
        data, label = await asyncio.gather(
            api_get(f"/wallets/{address}/pnl", params=params),
            _wallet_label(address),
        )

        if data is None:
            await cv2_error(interaction, "API Error", "Could not retrieve P&L data.")
            return

        if not isinstance(data, list) or not data:
            items = [
                _TextDisplay(f"## 🏦 Wallet P&L"),
                _Separator(spacing=_SeparatorSpacing.small),
                _TextDisplay(
                    f"`{short_addr(address)}`\n\n"
                    "No positions recorded yet.\n"
                    "P&L tracking starts automatically once whale alerts are detected."
                ),
            ]
            container = _Container(*items, accent_colour=COLOR_INFO)
            view = _LayoutView(timeout=None)
            view.add_item(container)
            await interaction.followup.send(view=view)
            return

        # ── Totals ────────────────────────────────────────────────────────────
        total_realized   = sum(pos["realized_pnl_usd"]          for pos in data)
        total_unrealized = sum(pos.get("unrealized_pnl_usd", 0) for pos in data)
        total_pnl        = total_realized + total_unrealized
        accent           = COLOR_BUY if total_pnl >= 0 else COLOR_SELL

        # ── Header ────────────────────────────────────────────────────────────
        wallet_name  = label or short_addr(address)
        chain_suffix = f" — {chain_badge(chain.value)}" if chain else ""
        header_text  = f"## 🏦 Wallet P&L — {wallet_name}{chain_suffix}"
        addr_text    = f"`{short_addr(address)}`"

        # ── Summary box ───────────────────────────────────────────────────────
        summary = _summary_box(total_realized, total_unrealized, total_pnl)

        # ── Per-chain breakdown ───────────────────────────────────────────────
        chain_groups: dict[str, list] = defaultdict(list)
        for pos in data:
            chain_groups[pos["chain"]].append(pos)

        # ── Build CV2 component tree ──────────────────────────────────────────
        items: list[Any] = [
            _TextDisplay(header_text),
            _Separator(spacing=_SeparatorSpacing.small),
            _TextDisplay(addr_text),
            _Separator(spacing=_SeparatorSpacing.small),
            _TextDisplay(summary),
        ]

        if chain_groups:
            items.append(_Separator(spacing=_SeparatorSpacing.large))
            items.append(_TextDisplay("**Per Chain Breakdown**"))

            for chain_name in sorted(chain_groups.keys()):
                items.append(_Separator(spacing=_SeparatorSpacing.small))
                items.append(_TextDisplay(_chain_section(chain_name, chain_groups[chain_name])))

        items.append(_Separator(spacing=_SeparatorSpacing.small, visible=False))
        items.append(_TextDisplay(f"-# {len(data)} position(s) — Smart Money Tracker"))

        container = _Container(*items, accent_colour=accent)
        view = _LayoutView(timeout=None)
        view.add_item(container)
        await interaction.followup.send(view=view)
