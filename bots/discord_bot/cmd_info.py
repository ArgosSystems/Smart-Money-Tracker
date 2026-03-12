"""
bots/discord_bot/cmd_info.py
------------------------------
Informational slash commands:

  /chains   - list all supported chains and their live status
  /status   - API health check with per-chain breakdown
  /invite   - generate the bot's Discord OAuth2 invite link
"""

from __future__ import annotations

import discord
from discord.ext import commands

import httpx

from config.settings import settings
from ._shared import (
    COLOR_BUY, COLOR_ERROR, COLOR_INFO,
    HEALTH_URL,
    api_get,
    chain_badge, cv2_error, cv2_send,
)


def setup_info(bot: commands.Bot) -> None:

    # /chains
    @bot.tree.command(
        name="chains",
        description="List all supported chains and their current status",
    )
    async def chains(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        data = await api_get("/chains")
        if not data:
            await cv2_error(interaction, "Could not load chain data")
            return
        lines: list[str] = []
        for c in data:
            status = "Active" if c["configured"] else "Not configured"
            lines.append(
                f"**{c['emoji']} {c['name'].capitalize()}**\n"
                f"Chain ID: `{c['chain_id']}` | Block time: ~{c['block_time']}s | Poll: {c['poll_interval']}s\n"
                f"Status: {status} | Explorer: {c['explorer']}"
            )
        await cv2_send(
            interaction,
            title="Supported Chains",
            lines=lines,
            color=COLOR_INFO,
            footer="Add RPC URLs to .env to enable inactive chains",
        )

    # /status
    @bot.tree.command(
        name="status",
        description="Check API and per-chain health",
    )
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(HEALTH_URL)
                resp.raise_for_status()
                h = resp.json()

            lines: list[str] = [
                f"**Whale Threshold:** ${h['whale_threshold_usd']:,.0f}",
            ]
            for chain_name, info in h.get("chains", {}).items():
                active = "Active" if info["configured"] else "Not configured"
                lines.append(
                    f"**{info['emoji']} {chain_name.capitalize()}:** {active} | Poll: {info['poll_interval']}s"
                )
            await cv2_send(
                interaction,
                title="API Online",
                lines=lines,
                color=COLOR_BUY,
            )
        except Exception:
            await cv2_error(
                interaction,
                "API Offline",
                "Start the API with `python start.py`.",
                ephemeral=False,
            )

    # /invite
    @bot.tree.command(
        name="invite",
        description="Get the OAuth2 link to add Smart Money Tracker to your server",
    )
    async def invite(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        invite_url = settings.discord_invite_url
        api_url    = settings.api_url
        ws_url     = settings.ws_url + "/ws/alerts"
        scopes     = settings.discord_oauth_scopes
        perms      = settings.discord_oauth_permissions

        if not invite_url:
            await cv2_send(
                interaction,
                title="Invite not configured",
                lines=[
                    "Set one of the following in your `.env` to generate an invite link:",
                    "**Option A (easiest):** `DISCORD_OAUTH_LINK=<your pre-built URL>`\nPaste the URL from **Discord Developer Portal → OAuth2 → URL Generator**.",
                    "**Option B (auto-build):** Set `DISCORD_CLIENT_ID=<id>`\nThe bot will build the invite URL from your Client ID, scopes, and permissions.",
                ],
                color=COLOR_ERROR,
                ephemeral=True,
            )
            return

        # Show whether the link came from a direct paste or was auto-built
        link_source = "Pasted directly (DISCORD_OAUTH_LINK)" if settings.discord_oauth_link else f"Auto-built from Client ID `{settings.discord_client_id}`"

        await cv2_send(
            interaction,
            title="Add Smart Money Tracker to your server",
            lines=[
                f"**Invite link:**\n{invite_url}",
                f"**Scopes:** `{scopes}`",
                f"**Permissions:** `{perms}`",
                f"**Link source:** {link_source}",
                f"**API endpoint:** `{api_url}`",
                f"**WebSocket stream:** `{ws_url}`",
                "After adding the bot, run `/help` to see all available commands.",
            ],
            color=COLOR_INFO,
            footer="Only server administrators can add bots.",
            ephemeral=True,
        )
