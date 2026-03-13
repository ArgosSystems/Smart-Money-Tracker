"""
bots/discord_bot/cmd_help.py
------------------------------
Help command:

  /help          - overview of all commands grouped by category
  /help <cmd>    - detailed usage for a specific command
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ._shared import COLOR_INFO, cv2_send

# ── Command catalogue ─────────────────────────────────────────────────────────

_COMMANDS: dict[str, dict] = {
    # Whale tracking
    "track_wallet": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "Start tracking a whale wallet on-chain.",
        "usage": "/track_wallet <address> [chain] [label]",
        "params": [
            ("address", "Required", "Wallet address (0x...)"),
            ("chain", "Optional", "Blockchain to monitor. Default: ethereum. Choices: ethereum, base, arbitrum, bsc, polygon, optimism"),
            ("label", "Optional", "A friendly nickname for this wallet"),
        ],
        "tip": "Use /whale_alerts after tracking to see the wallet's activity.",
    },
    "untrack_wallet": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "Stop tracking a whale wallet.",
        "usage": "/untrack_wallet <address> [chain]",
        "params": [
            ("address", "Required", "Wallet address to remove"),
            ("chain", "Optional", "Chain the wallet was tracked on. Default: ethereum"),
        ],
        "tip": "This removes the wallet from tracking but keeps its historical alerts.",
    },
    "whale_alerts": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "Show the most recent whale moves across all chains.",
        "usage": "/whale_alerts [chain] [count]",
        "params": [
            ("chain", "Optional", "Filter by a specific chain"),
            ("count", "Optional", "How many alerts to show (1-15). Default: 10"),
        ],
        "tip": "Omit chain to see alerts across all monitored networks at once.",
    },
    "smart_money": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "Whale buy/sell activity for a specific token.",
        "usage": "/smart_money <token> [chain]",
        "params": [
            ("token", "Required", "Token symbol (e.g. USDC) or contract address"),
            ("chain", "Optional", "Filter by chain. Leave blank for all chains"),
        ],
        "tip": "Check the sentiment summary to quickly see if whales are accumulating or distributing.",
    },
    "wallets": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "List all tracked whale wallets with labels and status.",
        "usage": "/wallets [chain]",
        "params": [
            ("chain", "Optional", "Filter by chain. Leave blank for all chains"),
        ],
        "tip": "Wallets with a label show it in bold next to the address.",
    },
    "trending": {
        "category": "Whale Tracking",
        "emoji": "🐋",
        "short": "Top tokens whales are currently accumulating.",
        "usage": "/trending [chain]",
        "params": [
            ("chain", "Optional", "Filter by chain. Leave blank for all"),
        ],
        "tip": "Combine with /smart_money to drill into a trending token.",
    },
    # Token Safety
    "scan_token": {
        "category": "Token Safety",
        "emoji": "◎",
        "short": "Solana token safety check — detect rug pulls, frozen wallets, and LP issues.",
        "usage": "/scan_token <mint>",
        "params": [
            ("mint", "Required", "Solana token mint address (base58)"),
        ],
        "tip": "Scores below 500 are SAFE. 500–1499 = CAUTION. 1500+ = DANGER. Always check LP lock % and mint authority.",
    },
    # Portfolio
    "portfolio_add": {
        "category": "Portfolio",
        "emoji": "📁",
        "short": "Add a wallet to portfolio balance tracking.",
        "usage": "/portfolio_add <address> [chain] [label]",
        "params": [
            ("address", "Required", "Wallet address (0x...)"),
            ("chain", "Optional", "Blockchain (default: ethereum)"),
            ("label", "Optional", "Nickname shown in /portfolio_list"),
        ],
        "tip": "After adding, run /portfolio_balance <id> to fetch the live on-chain balance.",
    },
    "portfolio_list": {
        "category": "Portfolio",
        "emoji": "📁",
        "short": "List all tracked wallets in your portfolio.",
        "usage": "/portfolio_list [chain]",
        "params": [
            ("chain", "Optional", "Filter by chain"),
        ],
        "tip": "Copy the wallet ID shown here to use with the other /portfolio_* commands.",
    },
    "portfolio_balance": {
        "category": "Portfolio",
        "emoji": "📁",
        "short": "Fetch live on-chain balance for a portfolio wallet.",
        "usage": "/portfolio_balance <wallet_id>",
        "params": [
            ("wallet_id", "Required", "Numeric ID from /portfolio_list"),
        ],
        "tip": "Balance is fetched from the RPC and saved as a snapshot automatically.",
    },
    "portfolio_remove": {
        "category": "Portfolio",
        "emoji": "📁",
        "short": "Delete a wallet and all its snapshots from the portfolio.",
        "usage": "/portfolio_remove <wallet_id>",
        "params": [
            ("wallet_id", "Required", "Numeric ID from /portfolio_list"),
        ],
        "tip": "This is irreversible — all saved snapshots will be deleted.",
    },
    "portfolio_toggle": {
        "category": "Portfolio",
        "emoji": "📁",
        "short": "Pause or resume automatic balance snapshots for a wallet.",
        "usage": "/portfolio_toggle <wallet_id>",
        "params": [
            ("wallet_id", "Required", "Numeric ID from /portfolio_list"),
        ],
        "tip": "Pausing stops new snapshots but keeps existing ones.",
    },
    # Price Alerts
    "price_alert_add": {
        "category": "Price Alerts",
        "emoji": "🔔",
        "short": "Create a price alert that fires when a token crosses a target.",
        "usage": "/price_alert_add <symbol> <address> <chain> <condition> <price> [label]",
        "params": [
            ("symbol", "Required", "Token ticker, e.g. PEPE"),
            ("address", "Required", "Token contract address (0x...)"),
            ("chain", "Required", "Blockchain the token lives on"),
            ("condition", "Required", "'above' fires when price rises past target, 'below' when it falls"),
            ("price", "Required", "Target price in USD, e.g. 0.00002"),
            ("label", "Optional", "A note to remind you why you set this alert"),
        ],
        "tip": "Alerts fire through the WebSocket stream and can be piped to a Discord channel.",
    },
    "price_alerts": {
        "category": "Price Alerts",
        "emoji": "🔔",
        "short": "List all price alert rules.",
        "usage": "/price_alerts [chain] [active_only]",
        "params": [
            ("chain", "Optional", "Filter by chain"),
            ("active_only", "Optional", "True to hide paused rules"),
        ],
        "tip": "Use the rule ID shown here with /price_alert_delete or /price_alert_toggle.",
    },
    "price_alert_delete": {
        "category": "Price Alerts",
        "emoji": "🔔",
        "short": "Permanently delete a price alert rule.",
        "usage": "/price_alert_delete <rule_id>",
        "params": [
            ("rule_id", "Required", "Numeric ID from /price_alerts"),
        ],
        "tip": "To keep the rule but stop it from firing, use /price_alert_toggle instead.",
    },
    "price_alert_toggle": {
        "category": "Price Alerts",
        "emoji": "🔔",
        "short": "Enable or disable a price alert rule without deleting it.",
        "usage": "/price_alert_toggle <rule_id>",
        "params": [
            ("rule_id", "Required", "Numeric ID from /price_alerts"),
        ],
        "tip": "Useful for temporarily pausing an alert while keeping it for later.",
    },
    # Info
    "chains": {
        "category": "Info",
        "emoji": "ℹ️",
        "short": "List all supported chains and their RPC / polling status.",
        "usage": "/chains",
        "params": [],
        "tip": "Chains without an RPC URL in .env show as Not configured.",
    },
    "status": {
        "category": "Info",
        "emoji": "ℹ️",
        "short": "Check whether the API server is online and each chain is active.",
        "usage": "/status",
        "params": [],
        "tip": "If the API is offline, start it with: python start.py",
    },
    "help": {
        "category": "Info",
        "emoji": "ℹ️",
        "short": "Show this help message.",
        "usage": "/help [command]",
        "params": [
            ("command", "Optional", "Name of a specific command to get detailed usage"),
        ],
        "tip": "Run /help without an argument to see all commands grouped by category.",
    },
    "invite": {
        "category": "Info",
        "emoji": "🔗",
        "short": "Generate the OAuth2 invite link for this bot.",
        "usage": "/invite",
        "params": [],
        "tip": "Requires DISCORD_CLIENT_ID to be set in .env. The link includes bot + applications.commands scopes by default.",
    },
}

# Build choices list (max 25 for Discord)
_HELP_CHOICES = [
    app_commands.Choice(name=f"{v['emoji']} /{k}", value=k)
    for k, v in _COMMANDS.items()
]


def _build_overview() -> list[str]:
    """Return CV2 lines for the full command overview."""
    categories: dict[str, list[str]] = {}
    for cmd, info in _COMMANDS.items():
        cat = info["category"]
        categories.setdefault(cat, [])
        categories[cat].append(f"• `/{ cmd }` — {info['short']}")

    cat_icons = {
        "Whale Tracking": "🐋",
        "Token Safety":   "◎",
        "Portfolio":      "📁",
        "Price Alerts":   "🔔",
        "Info":           "ℹ️",
    }
    lines = []
    for cat, cmds in categories.items():
        icon = cat_icons.get(cat, "")
        lines.append(f"**{icon} {cat}**\n" + "\n".join(cmds))
    return lines


def _build_detail(cmd_name: str) -> list[str]:
    """Return CV2 lines for a specific command's usage."""
    info = _COMMANDS[cmd_name]
    lines: list[str] = [
        f"**Category:** {info['emoji']} {info['category']}",
        f"**Description:** {info['short']}",
        f"**Usage:** `{info['usage']}`",
    ]
    if info["params"]:
        param_text = "\n".join(
            f"• `{name}` ({req}) — {desc}"
            for name, req, desc in info["params"]
        )
        lines.append(f"**Parameters:**\n{param_text}")
    if info.get("tip"):
        lines.append(f"**Tip:** {info['tip']}")
    return lines


# ── /help command ─────────────────────────────────────────────────────────────

def setup_help(bot: commands.Bot) -> None:

    @bot.tree.command(
        name="help",
        description="Show all commands or get detailed help for a specific command",
    )
    @app_commands.describe(command="Leave blank for an overview, or pick a command for detailed usage")
    @app_commands.choices(command=_HELP_CHOICES)
    async def help_cmd(
        interaction: discord.Interaction,
        command: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        if command is None:
            await cv2_send(
                interaction,
                title="Smart Money Tracker — Commands",
                lines=_build_overview(),
                color=COLOR_INFO,
                footer="Use /help <command> for detailed usage of any command.",
                ephemeral=True,
            )
        else:
            cmd_name = command.value
            if cmd_name not in _COMMANDS:
                await cv2_send(
                    interaction,
                    title="Unknown command",
                    lines=[f"No command named `{cmd_name}` found."],
                    color=discord.Color.red(),
                    ephemeral=True,
                )
                return
            info = _COMMANDS[cmd_name]
            await cv2_send(
                interaction,
                title=f"/{cmd_name}",
                lines=_build_detail(cmd_name),
                color=COLOR_INFO,
                footer="Smart Money Tracker",
                ephemeral=True,
            )