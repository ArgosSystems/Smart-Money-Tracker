"""
bots/discord_bot/cmd_exchange_flows.py
-----------------------------------------
Exchange flow detection slash command:

  /exchange_flows  [chain] [direction] [count]
    — Lists recent exchange flow events.
      OUTFLOW = whale → exchange (sell signal 🔴)
      INFLOW  = exchange → whale (buy signal 🟢)
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_SELL, COLOR_INFO,
    api_get,
    chain_badge, cv2_error, cv2_send,
    fmt_usd, short_addr, tx_link,
)

_DIRECTION_CHOICES = [
    app_commands.Choice(name="OUTFLOW — whale → exchange (sell 🔴)", value="OUTFLOW"),
    app_commands.Choice(name="INFLOW  — exchange → whale (buy 🟢)",  value="INFLOW"),
]


def setup_exchange_flows(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="exchange_flows",
        description="Recent whale exchange flow events (sells to / buys from exchanges)",
    )
    @app_commands.describe(
        chain="Filter by chain (leave blank for all)",
        direction="OUTFLOW = whale sold to exchange 🔴 | INFLOW = whale bought from exchange 🟢",
        count="Number of events to show (1-15)",
    )
    @app_commands.choices(chain=CHAIN_CHOICES, direction=_DIRECTION_CHOICES)
    async def exchange_flows(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
        direction: Optional[app_commands.Choice[str]] = None,
        count: Optional[int] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)

        count = max(1, min(count or 10, 15))
        params: dict = {"limit": count}
        if chain:
            params["chain"] = chain.value
        if direction:
            params["direction"] = direction.value

        data = await api_get("/exchange-flows", params=params)

        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch exchange flow events", "API error or no data.")
            return

        if not data:
            filter_desc = ""
            if chain:
                filter_desc += f" on {chain_badge(chain.value)}"
            if direction:
                filter_desc += f" ({direction.value})"
            await cv2_send(
                interaction,
                title="No exchange flow events detected",
                lines=[
                    f"No exchange flow events found{filter_desc}.",
                    "Events fire when a tracked whale sends to or receives from a known exchange address.",
                    "Make sure exchange addresses are in the SmartLabel table with entity_type = 'exchange'.",
                ],
                color=COLOR_INFO,
            )
            return

        # Build per-event lines (max 15 to stay under CV2 40-child limit)
        lines: list[str] = []
        for event in data:
            cname      = event.get("chain", "ethereum")
            direction_ = event.get("flow_direction", "OUTFLOW")
            exch_name  = event.get("exchange_name", "Unknown Exchange")
            wallet     = event.get("wallet_address", "")
            symbol     = event.get("token_symbol") or "native"
            amount_usd = event.get("amount_usd", 0.0)
            amount_tok = event.get("amount_token", 0.0)
            tx         = event.get("tx_hash", "")
            fired      = event.get("fired_at", "")[:16].replace("T", " ") if event.get("fired_at") else "?"

            signal_emoji = "🔴" if direction_ == "OUTFLOW" else "🟢"
            action_label = "→ exchange (SELL)" if direction_ == "OUTFLOW" else "← exchange (BUY)"

            link = tx_link(tx, cname) if tx else ""
            link_part = f"\n🔗 [{tx[:10]}…]({link})" if link else ""

            lines.append(
                f"{signal_emoji} **{symbol}** {CHAIN_EMOJI.get(cname, '')} {chain_badge(cname)}\n"
                f"👤 `{short_addr(wallet)}` {action_label} **{exch_name}**\n"
                f"💰 {fmt_usd(amount_usd)}  ·  {amount_tok:,.4f} {symbol}\n"
                f"🕒 {fired} UTC{link_part}"
            )

        direction_label = f" — {direction.value}" if direction else " — All"
        chain_label = f" {chain_badge(chain.value)}" if chain else " All Chains"
        title = f"Exchange Flow Alerts{chain_label}{direction_label}"

        # Choose color based on direction filter; default green (buy signals are noteworthy)
        if direction and direction.value == "OUTFLOW":
            color = COLOR_SELL
        elif direction and direction.value == "INFLOW":
            color = COLOR_BUY
        else:
            color = COLOR_INFO

        await cv2_send(
            interaction,
            title=title,
            lines=lines,
            color=color,
            footer="Smart Money Tracker — Exchange Flow Detection",
        )
