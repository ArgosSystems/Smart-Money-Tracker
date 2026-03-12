"""
bots/telegram_bot/bot.py
-------------------------
Telegram bot entry point.

Run
---
    python -m bots.telegram_bot.bot
"""

from __future__ import annotations

import logging
import sys

from telegram.ext import Application

from bots.telegram_bot.handlers import register_handlers
from config.settings import settings

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not settings.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set. Edit .env and restart.")
        sys.exit(1)

    app = Application.builder().token(settings.telegram_token).build()
    register_handlers(app)

    logger.info("Telegram bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
