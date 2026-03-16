"""
bots/discord_bot/cmd_twitter.py
---------------------------------
Admin-only Discord slash commands for Twitter broadcasting management.

Commands
--------
/twitter_status  — show circuit breaker state, queue depth, budget remaining
/twitter_test    — manually queue a specific alert for Twitter (dry-run preview)
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    COLOR_ERROR,
    COLOR_INFO,
    CHAIN_CHOICES,
    api_get,
    build_cv2,
    cv2_error,
    cv2_send,
    fmt_usd,
)

logger = logging.getLogger(__name__)


def setup_twitter(bot: commands.Bot) -> None:
    """Register Twitter admin commands with the bot."""

    @bot.tree.command(
        name="twitter_status",
        description="Show Twitter broadcaster status (admin only)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def twitter_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            data = await api_get("/twitter/status")
            if data is None:
                await interaction.followup.send(
                    "Twitter broadcasting is not enabled or the API is unreachable.",
                    ephemeral=True,
                )
                return

            mode = data.get("mode", "unknown").upper()
            running = "Yes" if data.get("running") else "No"
            queue_depth = data.get("queue_depth", 0)

            rl = data.get("rate_limiter", {})
            remaining_day = rl.get("remaining_today", "?")
            remaining_hour = rl.get("remaining_this_hour", "?")
            daily_budget = rl.get("daily_budget", "?")
            hourly_cap = rl.get("hourly_cap", "?")

            cb = data.get("circuit_breaker", {})
            cb_state = cb.get("state", "unknown").upper()
            cb_failures = cb.get("consecutive_failures", 0)

            features = data.get("features", {})
            whale = "On" if features.get("whale_tweets") else "Off"
            price = "On" if features.get("price_tweets") else "Off"
            portfolio = "On" if features.get("portfolio_tweets") else "Off"

            lines = [
                f"**Mode:** {mode}  |  **Running:** {running}",
                f"**Queue Depth:** {queue_depth} alerts pending",
                f"**Budget:** {remaining_day}/{daily_budget} today  |  {remaining_hour}/{hourly_cap} this hour",
                f"**Circuit Breaker:** {cb_state} ({cb_failures} consecutive failures)",
                f"**Features:** Whale={whale}  |  Price={price}  |  Portfolio={portfolio}",
            ]

            # Fetch recent tweets
            recent = await api_get("/twitter/recent", params={"limit": "5"})
            if recent:
                lines.append("")
                lines.append("**Last 5 tweets (dry-run):**")
                for post in recent:
                    status = post.get("tweet_id") or "dry-run"
                    content = post.get("content", "")[:60]
                    score = post.get("priority_score", 0)
                    lines.append(f"• [{score:.0f}pts] {content}… ({status})")

            text = "\n".join(lines)
            await interaction.followup.send(
                f"**Twitter Broadcaster Status**\n\n{text}",
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("twitter_status command failed: %s", exc)
            try:
                await interaction.followup.send(
                    f"An error occurred: {exc}", ephemeral=True
                )
            except Exception:
                pass

    @twitter_status.error
    async def twitter_status_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            try:
                await interaction.followup.send(
                    f"Command error: {error}", ephemeral=True
                )
            except Exception:
                pass

    @bot.tree.command(
        name="twitter_test",
        description="Preview/test a tweet for a specific alert (admin only)",
    )
    @app_commands.describe(
        alert_id="ID of the alert to test",
        alert_type="Type of alert (whale, price)",
    )
    @app_commands.choices(
        alert_type=[
            app_commands.Choice(name="Whale Alert", value="whale"),
            app_commands.Choice(name="Price Alert", value="price"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def twitter_test(
        interaction: discord.Interaction,
        alert_id: int,
        alert_type: str = "whale",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            data = await api_get(
                "/twitter/preview",
                params={"alert_id": str(alert_id), "alert_type": alert_type},
            )
            if data is None:
                await interaction.followup.send(
                    "Could not generate tweet preview. Check the alert ID and type.",
                    ephemeral=True,
                )
                return

            content = data.get("content", "(empty)")
            score = data.get("score", 0)
            char_count = len(content)
            gate = "✅ Would post" if data.get("would_post") else f"⛔ Skipped: {data.get('skip_reason', 'unknown')}"

            text = (
                f"**Twitter Test Preview — Alert #{alert_id} ({alert_type})**\n\n"
                f"**Score:** {score:.1f} pts  |  **Length:** {char_count}/280\n"
                f"**Gate:** {gate}\n\n"
                f"```\n{content}\n```"
            )
            await interaction.followup.send(text, ephemeral=True)

        except Exception as exc:
            logger.error("twitter_test command failed: %s", exc)
            try:
                await interaction.followup.send(
                    f"An error occurred: {exc}", ephemeral=True
                )
            except Exception:
                pass

    @twitter_test.error
    async def twitter_test_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            try:
                await interaction.followup.send(
                    f"Command error: {error}", ephemeral=True
                )
            except Exception:
                pass
