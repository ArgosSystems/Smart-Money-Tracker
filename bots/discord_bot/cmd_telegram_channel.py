"""
bots/discord_bot/cmd_telegram_channel.py
------------------------------------------
Admin-only Discord slash commands for Telegram Channel broadcaster management.

Commands
--------
/telegram_channel_status  — show circuit breaker state, queue depth, budget remaining
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import (
    COLOR_BUY,
    COLOR_ERROR,
    COLOR_INFO,
    api_get,
    api_post,
    cv2_error,
    cv2_send,
)

logger = logging.getLogger(__name__)


def setup_telegram_channel(bot: commands.Bot) -> None:
    """Register Telegram Channel admin commands with the bot."""

    @bot.tree.command(
        name="telegram_channel_status",
        description="Show Telegram Channel broadcaster status (admin only)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def telegram_channel_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            data = await api_get("/telegram-channel/status")
            if data is None:
                await interaction.followup.send(
                    "Telegram Channel broadcasting is not enabled or the API is unreachable.",
                    ephemeral=True,
                )
                return

            mode        = data.get("mode", "unknown").upper()
            running     = "Yes" if data.get("running") else "No"
            queue_depth = data.get("queue_depth", 0)
            channel_id  = data.get("channel_id", "?")

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
                f"**Channel:** `{channel_id}`",
                f"**Mode:** {mode}  |  **Running:** {running}",
                f"**Queue Depth:** {queue_depth} alerts pending",
                f"**Budget:** {remaining_day}/{daily_budget} today  |  {remaining_hour}/{hourly_cap} this hour",
                f"**Circuit Breaker:** {cb_state} ({cb_failures} consecutive failures)",
                f"**Features:** Whale={whale}  |  Price={price}  |  Portfolio={portfolio}  |  Accumulation={accumulation}",
                f"**Min Score:** {data.get('min_score', '?')}  |  **Critical Score:** {data.get('critical_score', '?')} (gets reserve)",
            ]

            # Recent posts
            recent = await api_get("/telegram-channel/recent", params={"limit": "5"})
            if recent:
                lines.append("")
                lines.append("**Last 5 messages (dry-run):**")
                for post in recent:
                    status  = post.get("message_id") or "dry-run"
                    content = (post.get("content") or "")[:60]
                    score   = post.get("priority_score", 0)
                    lines.append(f"• [{score:.0f}pts] {content}… ({status})")

            await interaction.followup.send(
                f"**Telegram Channel Broadcaster Status**\n\n" + "\n".join(lines),
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("telegram_channel_status command failed: %s", exc)
            try:
                await interaction.followup.send(f"An error occurred: {exc}", ephemeral=True)
            except Exception:
                pass

    # ── /telegram_channel_reset_budget ───────────────────────────────────────

    @bot.tree.command(
        name="telegram_channel_reset_budget",
        description="Reset the Telegram Channel rate-limiter so posting resumes immediately (admin only)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def telegram_channel_reset_budget(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        data, err = await api_post("/telegram-channel/reset-budget", {})
        if data is None:
            await cv2_error(interaction, "Reset failed", err or "API error", ephemeral=True)
            return

        rl = data.get("rate_limiter", {})
        await cv2_send(
            interaction,
            title="Telegram Channel Budget Reset",
            lines=[
                "Rate-limiter windows cleared — posting resumes immediately.",
                f"**Remaining today:** {rl.get('remaining_today', '?')}/{rl.get('daily_budget', '?')}",
                f"**Remaining this hour:** {rl.get('remaining_this_hour', '?')}/{rl.get('hourly_cap', '?')}",
            ],
            color=COLOR_BUY,
            ephemeral=True,
        )

    @telegram_channel_reset_budget.error
    async def telegram_channel_reset_budget_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )

    @telegram_channel_status.error
    async def telegram_channel_status_error(
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
