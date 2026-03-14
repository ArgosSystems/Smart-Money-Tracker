from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import api_patch, build_cv2, COLOR_INFO

logger = logging.getLogger(__name__)


@app_commands.guild_only()
class AdminGroup(app_commands.Group, name="admin", description="Admin commands (Bot Owners Only)"):

    @app_commands.command(name="upgrade_guild", description="Upgrade a Discord server to Pro tier")
    @app_commands.describe(guild_id="The ID of the Discord Server to upgrade")
    async def upgrade_guild(self, interaction: discord.Interaction, guild_id: str) -> None:
        # Simple hardcoded protection: bot owner check
        if not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            resp = await api_patch(f"/guilds/{guild_id}/upgrade")
            if resp and isinstance(resp, dict) and resp.get("tier") == "pro":
                view = build_cv2(
                    title="Guild Upgraded",
                    lines=[f"✅ Server `{guild_id}` successfully upgraded to **Pro Tier**!"],
                    color=COLOR_INFO,
                    footer="Smart Labeling System"
                )
                await interaction.followup.send(view=view)
            else:
                await interaction.followup.send(f"❌ Failed to upgrade: `{resp}`")
        except Exception as e:
            logger.error(f"Failed to upgrade guild {guild_id}: {e}")
            await interaction.followup.send(f"❌ API Error: {e}")


def setup_admin(bot: commands.Bot) -> None:
    bot.tree.add_command(AdminGroup())
