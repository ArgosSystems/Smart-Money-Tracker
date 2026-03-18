"""
bots/discord_bot/cmd_bluesky.py
---------------------------------
Admin-only Discord slash commands for Bluesky broadcaster management.

Commands
--------
/bluesky_status  — show circuit breaker state, queue depth, budget remaining
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    COLOR_ERROR,
    COLOR_INFO,
    api_get,
    cv2_error,
    cv2_send,
)

logger = logging.getLogger(__name__)


def setup_bluesky(bot: commands.Bot) -> None:
    """Register Bluesky admin commands with the bot."""

    @bot.tree.command(
        name="bluesky_status",
        description="Show Bluesky broadcaster status (admin only)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def bluesky_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            data = await api_get("/bluesky/status")
            if data is None:
                await interaction.followup.send(
                    "Bluesky broadcasting is not enabled or the API is unreachable.",
                    ephemeral=True,
                )
                return

            mode        = data.get("mode", "unknown").upper()
            running     = "Yes" if data.get("running") else "No"
            queue_depth = data.get("queue_depth", 0)
            handle      = data.get("handle", "?")

            rl             = data.get("rate_limiter", {})
            remaining_day  = rl.get("remaining_today", "?")
            remaining_hour = rl.get("remaining_this_hour", "?")
            daily_budget   = rl.get("daily_budget", "?")
            hourly_cap     = rl.get("hourly_cap", "?")

            cb          = data.get("circuit_breaker", {})
            cb_state    = cb.get("state", "unknown").upper()
            cb_failures = cb.get("consecutive_failures", 0)

            features      = data.get("features", {})
            whale         = "On" if features.get("whale_posts") else "Off"
            price         = "On" if features.get("price_posts") else "Off"
            portfolio     = "On" if features.get("portfolio_posts") else "Off"
            accumulation  = "On" if features.get("accumulation_posts") else "Off"

            lines = [
                f"**Handle:** `@{handle}`",
                f"**Mode:** {mode}  |  **Running:** {running}",
                f"**Queue Depth:** {queue_depth} alerts pending",
                f"**Budget:** {remaining_day}/{daily_budget} today  |  {remaining_hour}/{hourly_cap} this hour",
                f"**Circuit Breaker:** {cb_state} ({cb_failures} consecutive failures)",
                f"**Features:** Whale={whale}  |  Price={price}  |  Portfolio={portfolio}  |  Accumulation={accumulation}",
            ]

            # Recent posts
            recent = await api_get("/bluesky/recent", params={"limit": "5"})
            if recent:
                lines.append("")
                lines.append("**Last 5 posts (dry-run):**")
                for post in recent:
                    status  = post.get("post_uri") or "dry-run"
                    content = (post.get("content") or "")[:60]
                    score   = post.get("priority_score", 0)
                    lines.append(f"• [{score:.0f}pts] {content}… ({status})")

            await interaction.followup.send(
                f"**Bluesky Broadcaster Status**\n\n" + "\n".join(lines),
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("bluesky_status command failed: %s", exc)
            try:
                await interaction.followup.send(f"An error occurred: {exc}", ephemeral=True)
            except Exception:
                pass

    @bluesky_status.error
    async def bluesky_status_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            try:
                await interaction.followup.send(f"Command error: {error}", ephemeral=True)
            except Exception:
                pass
