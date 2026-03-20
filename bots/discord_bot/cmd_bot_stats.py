"""
bots/discord_bot/cmd_bot_stats.py
-----------------------------------
Bot accuracy stats slash command:

  /bot_stats  — show win rate and avg P&L per signal type
"""

from __future__ import annotations

import discord
from discord.ext import commands

from ._shared import (
    COLOR_BUY, COLOR_INFO,
    api_get,
    cv2_error, cv2_send,
)


def setup_bot_stats(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="bot_stats",
        description="How accurate is Smart Money Tracker? Win rates and avg gains per signal type",
    )
    async def bot_stats(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        data = await api_get("/backtest/stats")

        if not isinstance(data, dict):
            await cv2_error(
                interaction,
                "Could not fetch accuracy stats",
                "API error or no backtest data yet. Try /bot_stats again after some signals are recorded.",
            )
            return

        acc  = data.get("accumulation",  {})
        exfl = data.get("exchange_flow", {})

        def _fmt_block(label: str, emoji: str, stats: dict) -> str:
            total   = stats.get("total_signals", 0)
            correct = stats.get("total_correct", 0)
            wr      = stats.get("win_rate_pct", 0.0)
            p24     = stats.get("avg_pnl_24h_pct", 0.0)
            p72     = stats.get("avg_pnl_72h_pct", 0.0)
            p7d     = stats.get("avg_pnl_7d_pct",  0.0)

            if total == 0:
                return f"{emoji} **{label}**\nNo signals processed yet."

            sign_72 = "+" if p72 >= 0 else ""
            sign_7d = "+" if p7d >= 0 else ""

            return (
                f"{emoji} **{label}**\n"
                f"Win rate: **{wr:.0f}%** ({correct}/{total} signals correct)\n"
                f"Avg gain: **{sign_72}{p72:.1f}%** at 72h · **{sign_7d}{p7d:.1f}%** at 7d"
            )

        acc_block  = _fmt_block("Accumulation Signals",  "🎯", acc)
        exfl_block = _fmt_block("Exchange Flow Signals",  "📡", exfl)

        generated = data.get("generated_at", "")[:16].replace("T", " ")

        lines = [
            acc_block,
            exfl_block,
            (
                "**How we measure accuracy**\n"
                "A signal is *correct* if the token price moved >5% in the predicted direction "
                "within 72 hours of the alert firing.\n"
                "Price data: DeFiLlama historical API."
            ),
        ]

        await cv2_send(
            interaction,
            title="Smart Money Tracker — Accuracy Stats",
            lines=lines,
            color=COLOR_BUY,
            footer=f"Based on all signals since launch · Updated: {generated} UTC",
        )
