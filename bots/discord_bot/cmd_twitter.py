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

        data = await api_get("/twitter/status")
        if data is None:
            await cv2_error(
                interaction,
                "Twitter Not Available",
                "Twitter broadcasting is not enabled or the API is unreachable.",
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
            lines.append("**Last 5 tweets:**")
            for post in recent:
                status = f"ID: {post.get('tweet_id', 'dry-run')}"
                content = post.get("content", "")[:60]
                score = post.get("priority_score", 0)
                lines.append(f"- [{score:.0f}pts] {content}… ({status})")

        await cv2_send(
            interaction,
            title="Twitter Broadcaster Status",
            lines=lines,
            color=COLOR_INFO,
            ephemeral=True,
        )

    @twitter_status.error
    async def twitter_status_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )

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

        data = await api_get(
            "/twitter/preview",
            params={"alert_id": str(alert_id), "alert_type": alert_type},
        )
        if data is None:
            await cv2_error(
                interaction,
                "Preview Failed",
                "Could not generate tweet preview. Check alert ID and type.",
            )
            return

        content = data.get("content", "(empty)")
        score = data.get("score", 0)
        char_count = len(content)

        lines = [
            f"**Score:** {score:.1f} pts  |  **Length:** {char_count}/280 chars",
            "",
            f"```\n{content}\n```",
        ]

        if data.get("would_post"):
            lines.append("This alert **would be posted** (passes all gates).")
        else:
            reason = data.get("skip_reason", "unknown")
            lines.append(f"This alert would be **skipped**: {reason}")

        await cv2_send(
            interaction,
            title="Twitter Test Preview",
            lines=lines,
            color=COLOR_INFO,
            footer=f"Alert #{alert_id} ({alert_type})",
            ephemeral=True,
        )

    @twitter_test.error
    async def twitter_test_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
