"""
bots/discord_bot/cmd_accumulation.py
---------------------------------------
Accumulation detection slash command:

  /accumulation_alerts  [chain] [count]  — recent accumulation pattern alerts
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_INFO,
    api_get,
    chain_badge, chain_color, cv2_error, cv2_send,
    fmt_usd, short_addr,
)


def setup_accumulation(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="accumulation_alerts",
        description="Show wallets that are repeatedly accumulating the same token",
    )
    @app_commands.describe(
        chain="Filter by chain (leave blank for all)",
        count="Number of alerts to show (1-20)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def accumulation_alerts(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
        count: Optional[int] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)

        count = max(1, min(count or 10, 20))
        params: dict = {"limit": count}
        if chain:
            params["chain"] = chain.value

        data = await api_get("/alerts/accumulations", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch accumulation alerts", "API error or no data.")
            return

        if not data:
            await cv2_send(
                interaction,
                title="No accumulation patterns detected",
                lines=[
                    "No wallet has triggered the accumulation pattern yet.",
                    "Pattern fires when a wallet buys the same token ≥ 3 times in 24 h with ≥ $50K volume.",
                ],
                color=COLOR_INFO,
            )
            return

        title_chain = f" — {chain_badge(chain.value)}" if chain else " — All Chains"
        lines: list[str] = []

        for event in data:
            cname   = event.get("chain", "ethereum")
            symbol  = event.get("token_symbol") or "Unknown"
            wallet  = event.get("wallet_address", "")
            buys    = event.get("buy_count", 0)
            total   = event.get("total_usd", 0.0)
            avg     = event.get("avg_per_tx_usd", 0.0)
            window  = event.get("window_hours", 24)
            fired   = event.get("fired_at", "")[:16].replace("T", " ") if event.get("fired_at") else "?"

            lines.append(
                f"🔁 **{symbol}** {CHAIN_EMOJI.get(cname, '')} {chain_badge(cname)}\n"
                f"Wallet: `{short_addr(wallet)}`\n"
                f"{buys} buys in {window}h — Total: {fmt_usd(total)} — Avg: {fmt_usd(avg)}/tx\n"
                f"Detected: {fired} UTC"
            )

        await cv2_send(
            interaction,
            title=f"Accumulation Alerts{title_chain}",
            lines=lines,
            color=COLOR_BUY,
            footer="Smart Money Tracker — Accumulation Detection",
        )
