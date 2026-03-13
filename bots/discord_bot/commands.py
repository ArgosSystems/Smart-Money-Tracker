"""
bots/discord_bot/commands.py
-----------------------------
Entry point — registers all slash commands by calling each module's
setup_*() function.

Command groups
--------------
  Whale tracking  (cmd_whale.py)         — /track_wallet, /untrack_wallet, /wallets,
                                           /whale_alerts, /smart_money, /trending
  Token Safety    (cmd_token_safety.py)  — /scan_token
  Portfolio       (cmd_portfolio.py)     — /portfolio_add, /portfolio_list,
                                           /portfolio_balance, /portfolio_remove,
                                           /portfolio_toggle
  Price alerts    (cmd_price_alerts.py)  — /price_alert_add, /price_alerts,
                                           /price_alert_delete, /price_alert_toggle
  Info            (cmd_info.py)          — /chains, /status
  Help            (cmd_help.py)          — /help
"""

from __future__ import annotations

from discord.ext import commands

from .cmd_whale import setup_whale
from .cmd_token_safety import setup_token_safety
from .cmd_portfolio import setup_portfolio
from .cmd_price_alerts import setup_price_alerts
from .cmd_info import setup_info
from .cmd_help import setup_help


def setup_commands(bot: commands.Bot) -> None:
    """Register every slash command group with the bot."""
    setup_whale(bot)
    setup_token_safety(bot)
    setup_portfolio(bot)
    setup_price_alerts(bot)
    setup_info(bot)
    setup_help(bot)