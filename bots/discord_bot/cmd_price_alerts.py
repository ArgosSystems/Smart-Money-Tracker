"""
bots/discord_bot/cmd_price_alerts.py
--------------------------------------
Price-alert slash commands:

  /price_alert_add     <symbol> <address> <chain> <condition> <price> [label]
  /price_alerts        [chain] [active_only]
  /price_alert_delete  <id>
  /price_alert_toggle  <id>
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    CHAIN_CHOICES, CHAIN_EMOJI,
    COLOR_BUY, COLOR_ERROR, COLOR_INFO, COLOR_WARN,
    api_delete, api_get, api_patch, api_post,
    chain_badge, chain_color, cv2_error, cv2_send, fmt_price, short_addr,
)

CONDITION_CHOICES = [
    app_commands.Choice(name="Above (price rises past target)", value="above"),
    app_commands.Choice(name="Below (price falls past target)", value="below"),
]


def setup_price_alerts(bot: commands.Bot) -> None:

    # /price_alert_add
    @bot.tree.command(
        name="price_alert_add",
        description="Create a price alert - triggers when a token crosses your target",
    )
    @app_commands.describe(
        symbol="Token symbol, e.g. PEPE",
        address="Token contract address (0x...)",
        chain="Blockchain the token lives on",
        condition="Trigger when price goes above or below the target",
        price="Target price in USD (e.g. 0.00002)",
        label="Optional note for this alert",
    )
    @app_commands.choices(chain=CHAIN_CHOICES, condition=CONDITION_CHOICES)
    async def price_alert_add(
        interaction: discord.Interaction,
        symbol: str,
        address: str,
        chain: app_commands.Choice[str],
        condition: app_commands.Choice[str],
        price: float,
        label: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        payload: dict = {
            "chain": chain.value,
            "token_address": address,
            "token_symbol": symbol.upper(),
            "condition": condition.value,
            "target_price_usd": price,
        }
        if label:
            payload["label"] = label
        data, err = await api_post("/price-alerts", payload)
        if data is None:
            await cv2_error(interaction, "Failed to create alert", err or "Unknown error.")
            return
        cname     = data.get("chain", chain.value)
        cond_icon = "above" if condition.value == "above" else "below"
        lines = [
            f"**Token:** `{symbol.upper()}`",
            f"**Condition:** {cond_icon} {condition.name}",
            f"**Target:** {fmt_price(price)}",
            f"**Chain:** {chain_badge(cname)}",
            f"**Address:** `{short_addr(address)}`",
        ]
        if data.get("label"):
            lines.append(f"**Label:** {data['label']}")
        await cv2_send(
            interaction,
            title=f"Price alert created - {symbol.upper()}",
            lines=lines,
            color=chain_color(cname),
            footer=f"ID: {data['id']} - Alert fires via WebSocket + this channel",
        )

    # /price_alerts
    @bot.tree.command(
        name="price_alerts",
        description="List your price alert rules",
    )
    @app_commands.describe(
        chain="Filter by chain (leave blank for all)",
        active_only="Show only enabled rules",
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def price_alerts(
        interaction: discord.Interaction,
        chain: Optional[app_commands.Choice[str]] = None,
        active_only: Optional[bool] = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        params: dict = {}
        if chain:
            params["chain"] = chain.value
        if active_only:
            params["active_only"] = "true"
        data = await api_get("/price-alerts", params=params)
        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch alerts")
            return
        if not data:
            await cv2_send(
                interaction,
                title="No price alerts",
                lines=["Use `/price_alert_add` to create your first rule."],
                color=COLOR_INFO,
            )
            return
        title = f"Price Alerts{' - ' + chain_badge(chain.value) if chain else ' - All Chains'}"
        lines: list[str] = []
        for rule in data[:20]:
            cname     = rule.get("chain", "ethereum")
            is_active = rule.get("is_active", True)
            cond      = rule["condition"].capitalize()
            status    = "Active" if is_active else "Paused"
            triggered = rule.get("last_triggered_at")
            lbl       = f"\nLabel: {rule['label']}" if rule.get("label") else ""
            lines.append(
                f"**ID {rule['id']} - {status} {rule['token_symbol']}**  {CHAIN_EMOJI.get(cname, '')}\n"
                f"{cond} **{fmt_price(rule['target_price_usd'])}**  |  Chain: {chain_badge(cname)}"
                + lbl
                + f"\nLast hit: {triggered[:10] if triggered else 'Never'}"
            )
        footer = f"Showing 20 of {len(data)} rules" if len(data) > 20 else ""
        await cv2_send(interaction, title=title, lines=lines, color=COLOR_INFO, footer=footer)

    # /price_alert_delete
    @bot.tree.command(
        name="price_alert_delete",
        description="Delete a price alert rule permanently",
    )
    @app_commands.describe(rule_id="Price alert ID (see /price_alerts)")
    async def price_alert_delete(
        interaction: discord.Interaction,
        rule_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data = await api_delete(f"/price-alerts/{rule_id}")
        if data is None:
            await cv2_error(
                interaction,
                "Could not delete alert",
                f"Rule ID `{rule_id}` not found or API error.",
            )
            return
        await cv2_send(
            interaction,
            title="Price alert deleted",
            lines=[f"Rule ID `{rule_id}` has been removed."],
            color=COLOR_WARN,
        )

    # /price_alert_toggle
    @bot.tree.command(
        name="price_alert_toggle",
        description="Enable or disable a price alert rule",
    )
    @app_commands.describe(rule_id="Price alert ID (see /price_alerts)")
    async def price_alert_toggle(
        interaction: discord.Interaction,
        rule_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data, err = await api_patch(f"/price-alerts/{rule_id}/toggle")
        if data is None:
            await cv2_error(
                interaction,
                "Could not toggle alert",
                err or f"Rule ID `{rule_id}` not found.",
            )
            return
        is_active = data.get("is_active", False)
        status    = "Active" if is_active else "Paused"
        action    = "enabled" if is_active else "paused"
        await cv2_send(
            interaction,
            title=f"Price alert {action}",
            lines=[f"Rule ID `{rule_id}` is now **{status}**."],
            color=COLOR_BUY if is_active else COLOR_WARN,
        )