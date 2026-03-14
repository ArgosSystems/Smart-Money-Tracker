"""
bots/discord_bot/cmd_alert_channels.py
----------------------------------------
Slash commands for configuring real-time alert push channels:

  /set_alert_channel   [min_score] [chains] [alert_types]
  /alert_channels      — list configured channels for this server
  /remove_alert_channel <id>
  /toggle_alert_channel <id>
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    COLOR_BUY, COLOR_ERROR, COLOR_INFO, COLOR_WARN,
    api_delete, api_get, api_patch, api_post,
    cv2_error, cv2_send,
)


def setup_alert_channels(bot: commands.Bot) -> None:

    # /set_alert_channel
    @bot.tree.command(
        name="set_alert_channel",
        description="Enable real-time whale/price alert push in this channel",
    )
    @app_commands.describe(
        min_score="Minimum priority score to push (0-100, default: 0)",
        chains="Filter by chains (comma-separated, e.g. ethereum,bsc). Leave blank for all.",
        alert_types="Filter by alert types (comma-separated: whale,price). Leave blank for all.",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def set_alert_channel(
        interaction: discord.Interaction,
        min_score: Optional[float] = 0.0,
        chains: Optional[str] = None,
        alert_types: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        payload = {
            "guild_id": str(interaction.guild_id),
            "channel_id": str(interaction.channel_id),
            "min_score": min_score or 0.0,
        }
        if chains:
            payload["chains"] = chains
        if alert_types:
            payload["alert_types"] = alert_types

        data, err = await api_post("/alert-channels", payload)
        if data is None:
            await cv2_error(interaction, "Failed to configure channel", err or "Unknown error.")
            return

        lines = [
            f"**Channel:** <#{interaction.channel_id}>",
            f"**Min Score:** {data.get('min_score', 0.0)}",
        ]
        if data.get("chains"):
            lines.append(f"**Chains:** {data['chains']}")
        else:
            lines.append("**Chains:** All")
        if data.get("alert_types"):
            lines.append(f"**Alert Types:** {data['alert_types']}")
        else:
            lines.append("**Alert Types:** All")
        lines.append(f"**Status:** {'Active' if data.get('is_active') else 'Inactive'}")

        await cv2_send(
            interaction,
            title="Alert Channel Configured",
            lines=lines,
            color=COLOR_BUY,
            footer=f"ID: {data['id']} | Alerts will be pushed here automatically",
        )

    @set_alert_channel.error
    async def set_alert_channel_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Manage Channels** permission to configure alert channels.",
                ephemeral=True,
            )

    # /alert_channels
    @bot.tree.command(
        name="alert_channels",
        description="List all configured alert channels in this server",
    )
    async def alert_channels(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        data = await api_get("/alert-channels", params={"guild_id": str(interaction.guild_id)})
        if not isinstance(data, list):
            await cv2_error(interaction, "Could not fetch channels", "API error.")
            return
        if not data:
            await cv2_send(
                interaction,
                title="No Alert Channels",
                lines=["Use `/set_alert_channel` to enable real-time alerts in a channel."],
                color=COLOR_INFO,
            )
            return

        lines: list[str] = []
        for ch in data:
            status = "Active" if ch.get("is_active") else "Inactive"
            status_emoji = "🟢" if ch.get("is_active") else "⚪"
            chains_str = ch.get("chains") or "All"
            types_str = ch.get("alert_types") or "All"
            lines.append(
                f"{status_emoji} <#{ch['channel_id']}> — **{status}**\n"
                f"Min Score: {ch.get('min_score', 0)} | Chains: {chains_str} | Types: {types_str}\n"
                f"ID: `{ch['id']}`"
            )
        await cv2_send(
            interaction,
            title="Alert Channels",
            lines=lines,
            color=COLOR_INFO,
            footer=f"Total: {len(data)} channel(s)",
        )

    # /remove_alert_channel
    @bot.tree.command(
        name="remove_alert_channel",
        description="Remove an alert channel configuration",
    )
    @app_commands.describe(channel_id="The config ID (from /alert_channels)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def remove_alert_channel(
        interaction: discord.Interaction,
        channel_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        result = await api_delete(f"/alert-channels/{channel_id}")
        if result is None:
            await cv2_error(interaction, "Failed to remove", "Channel config not found or API error.")
            return
        await cv2_send(
            interaction,
            title="Alert Channel Removed",
            lines=[f"Configuration `{channel_id}` has been removed. Alerts will no longer be pushed."],
            color=COLOR_WARN,
        )

    @remove_alert_channel.error
    async def remove_alert_channel_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Manage Channels** permission to remove alert channels.",
                ephemeral=True,
            )

    # /toggle_alert_channel
    @bot.tree.command(
        name="toggle_alert_channel",
        description="Enable or disable an alert channel",
    )
    @app_commands.describe(channel_id="The config ID (from /alert_channels)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def toggle_alert_channel(
        interaction: discord.Interaction,
        channel_id: int,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data, err = await api_patch(f"/alert-channels/{channel_id}/toggle")
        if data is None:
            await cv2_error(interaction, "Failed to toggle", err or "Channel config not found.")
            return
        status = "Active" if data.get("is_active") else "Inactive"
        emoji = "🟢" if data.get("is_active") else "⚪"
        await cv2_send(
            interaction,
            title=f"Alert Channel {status}",
            lines=[f"{emoji} <#{data['channel_id']}> is now **{status}**"],
            color=COLOR_BUY if data.get("is_active") else COLOR_WARN,
        )

    @toggle_alert_channel.error
    async def toggle_alert_channel_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Manage Channels** permission to toggle alert channels.",
                ephemeral=True,
            )
