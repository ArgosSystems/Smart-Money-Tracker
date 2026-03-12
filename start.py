"""
start.py
--------
Unified launcher.  Runs the FastAPI backend + Discord bot in the same
process using asyncio so you only need a single terminal.

Usage
-----
    python start.py              # API + Discord bot
    python start.py --api-only   # just the API
    python start.py --bot-only   # just the Discord bot (API must already run)
    python start.py --telegram   # API + Telegram bot instead of Discord
"""

from __future__ import annotations

# ── Auto-relaunch under the virtual environment ───────────────────────────────
# On Windows, `python` resolves to a silent App Execution Alias stub rather
# than a real interpreter.  If we are NOT already running inside the project
# .venv, re-exec this script with .venv/Scripts/python.exe automatically.
import os as _os, sys as _sys, subprocess as _subprocess

def _ensure_venv() -> None:
    venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
    venv_python = _os.path.join(
        venv_dir,
        "Scripts" if _sys.platform == "win32" else "bin",
        "python.exe" if _sys.platform == "win32" else "python",
    )
    # If we're already inside the venv, or the venv doesn't exist yet, skip
    running_in_venv = _os.path.normcase(_sys.executable).startswith(
        _os.path.normcase(venv_dir)
    )
    if running_in_venv or not _os.path.isfile(venv_python):
        return
    # Re-exec with the venv Python, passing all original arguments
    _sys.stdout.flush()
    result = _subprocess.run([venv_python] + _sys.argv)
    _sys.exit(result.returncode)

_ensure_venv()
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import logging
import signal
import sys

import uvicorn

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smart Money Tracker launcher")
    p.add_argument("--api-only",  action="store_true", help="Run only the FastAPI backend")
    p.add_argument("--bot-only",  action="store_true", help="Run only the Discord bot")
    p.add_argument("--telegram",  action="store_true", help="Run Telegram bot instead of Discord")
    return p.parse_args()


async def run_api() -> None:
    """Start uvicorn in-process (non-blocking)."""
    from config.settings import settings

    config = uvicorn.Config(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_discord_bot() -> None:
    """Start the Discord bot."""
    from bots.discord_bot.bot import create_bot
    from bots.discord_bot.commands import setup_commands
    from config.settings import settings

    if not settings.discord_token:
        logger.error("DISCORD_TOKEN missing — Discord bot will not start.")
        return

    bot = create_bot()
    setup_commands(bot)
    await bot.start(settings.discord_token)


async def run_telegram_bot() -> None:
    """Start the Telegram bot."""
    from config.settings import settings

    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN missing — Telegram bot will not start.")
        return

    from telegram.ext import Application
    from bots.telegram_bot.handlers import register_handlers

    app = Application.builder().token(settings.telegram_token).build()
    register_handlers(app)

    async with app:
        await app.start()
        assert app.updater is not None, "Telegram Application has no updater"
        await app.updater.start_polling()
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()


async def main_async(args: argparse.Namespace) -> None:
    tasks: list[asyncio.Task] = []

    if not args.bot_only:
        tasks.append(asyncio.create_task(run_api(), name="api"))

    if not args.api_only:
        if args.telegram:
            tasks.append(asyncio.create_task(run_telegram_bot(), name="telegram_bot"))
        else:
            tasks.append(asyncio.create_task(run_discord_bot(), name="discord_bot"))

    if not tasks:
        logger.error("No services selected to run.")
        return

    def _shutdown(*_) -> None:
        logger.info("Shutdown signal received...")
        for t in tasks:
            t.cancel()

    # Windows does not support loop.add_signal_handler — use signal.signal instead
    import sys as _sys
    if _sys.platform != "win32":
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT,  _shutdown)
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
    # On Windows: asyncio.run() propagates KeyboardInterrupt (Ctrl+C) as
    # CancelledError to all running tasks automatically — no signal.signal
    # needed, and installing one risks firing on a stale pending signal from
    # a previous run being killed.

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("All services stopped.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args = parse_args()

    print(
        "\n"
        "  +======================================+\n"
        "  |    Smart Money Tracker  (whale)      |\n"
        "  +======================================+\n"
    )

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
