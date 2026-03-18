"""
bots/discord_bot/cmd_pnl.py
-----------------------------
Wallet P&L slash commands:

  /wallet_pnl  <address> [chain]   — show realized P&L for all tracked tokens
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_ERROR, COLOR_INFO, COLOR_SELL, COLOR_WARN,
    api_get,
    chain_badge, chain_color, cv2_error, cv2_send,
    fmt_usd, short_addr,
)


def setup_pnl(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="wallet_pnl",
        description="Show realized P&L for all tracked tokens in a wallet",
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

        data = await api_get(f"/wallets/{address}/pnl", params=params)

        if data is None:
            await cv2_error(interaction, "API Error", "Could not retrieve P&L data.")
            return

        if not isinstance(data, list) or not data:
            await cv2_send(
                interaction,
                title="No P&L data yet",
                lines=[
                    f"`{address}` has no recorded positions.",
                    "P&L tracking starts automatically once whale alerts are detected for this wallet.",
                ],
                color=COLOR_INFO,
            )
            return

        title_chain = f" — {chain_badge(chain.value)}" if chain else " — All Chains"
        total_realized  = sum(pos["realized_pnl_usd"]   for pos in data)
        total_unrealized = sum(pos.get("unrealized_pnl_usd", 0.0) for pos in data)
        total_pnl       = total_realized + total_unrealized
        total_color     = COLOR_BUY if total_pnl >= 0 else COLOR_SELL

        def _signed(v: float) -> str:
            return f"+{fmt_usd(v)}" if v >= 0 else fmt_usd(v)

        lines: list[str] = [
            f"**Wallet:** `{short_addr(address)}`",
            f"**Realized P&L:** {_signed(total_realized)}",
            f"**Unrealized P&L:** {_signed(total_unrealized)}",
            f"**Total P&L:** **{_signed(total_pnl)}**",
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
                f"{pnl_emoji} **{symbol}** {CHAIN_EMOJI.get(cname, '')} — Total: {_signed(total)}\n"
                f"Realized: {_signed(realized)}  |  Unrealized: {_signed(unrealized)}\n"
                f"Bought: {fmt_usd(bought)}  |  Sold: {fmt_usd(sold)}  |  Avg: ${avg:,.4f}  |  Txs: {txs}"
            )

        await cv2_send(
            interaction,
            title=f"Wallet P&L{title_chain}",
            lines=lines,
            color=total_color,
            footer=f"{len(data)} position(s) — Smart Money Tracker",
        )
