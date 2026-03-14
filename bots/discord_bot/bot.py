"""
bots/discord_bot/bot.py
------------------------
Discord bot entry point.

The bot communicates ONLY with the local FastAPI backend — it never
touches the blockchain or database directly.  This is what makes the
architecture reusable: swap the bot for Telegram, a web dashboard, or
a Slack app and the core logic stays unchanged.

Run
---
    python -m bots.discord_bot.bot
"""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from bots.discord_bot.commands import setup_commands
from bots.discord_bot.auto_push import start_auto_push, stop_auto_push
from config.settings import settings

logger = logging.getLogger(__name__)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True   # needed for prefix commands (if any)

    bot = commands.Bot(
        command_prefix="!",           # prefix fallback (slash commands are primary)
        intents=intents,
        help_command=None,            # we'll implement our own
    )

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "N/A")
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %d slash command(s).", len(synced))
        except Exception as exc:
            logger.error("Failed to sync commands: %s", exc)
        # Start real-time alert auto-push to configured channels
        await start_auto_push(bot)

    @bot.event
    async def on_command_error(ctx: commands.Context, error: Exception) -> None:
        logger.error("Command error: %s", error)

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        logger.info("Joined guild %s (%s). Registering tier.", guild.name, guild.id)
        try:
            from bots.discord_bot._shared import api_post
            await api_post("/guilds", {"guild_id": str(guild.id)})
        except Exception as exc:
            logger.error("Failed to register guild %s: %s", guild.id, exc)

    return bot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not settings.discord_token:
        logger.error("DISCORD_TOKEN is not set. Edit .env and restart.")
        sys.exit(1)

    bot = create_bot()
    setup_commands(bot)   # register all slash commands
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
