"""
api/services/telegram_channel/client.py
-----------------------------------------
Telegram Channel Bot API client.

Posts HTML-formatted messages to a public or private Telegram channel.
The bot must be an administrator of the target channel with posting rights.

Uses python-telegram-bot (already a project dependency — no new install needed).
The library is natively async so no asyncio.to_thread() wrapper is required.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TelegramChannelClientError(Exception):
    """Raised when a Telegram Bot API call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelegramChannelClient:
    """
    Posts messages to a Telegram channel via the Bot API.

    Usage
    -----
        client = TelegramChannelClient(bot_token="...", channel_id="@mychannel")
        msg_id = await client.post_message("<b>Whale Alert!</b>\\n...")
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._bot: object | None = None

    def _get_bot(self) -> object:
        """Lazily create the telegram.Bot instance."""
        if self._bot is not None:
            return self._bot
        try:
            from telegram import Bot  # noqa: PLC0415
        except ImportError as exc:
            raise TelegramChannelClientError(
                "python-telegram-bot is not installed. Run: pip install python-telegram-bot"
            ) from exc
        self._bot = Bot(token=self._bot_token)
        logger.info("TelegramChannelClient initialized (channel=%s)", self._channel_id)
        return self._bot

    async def post_message(self, text: str) -> str:
        """
        Post an HTML-formatted message to the channel.

        Returns the message_id as a string.
        Raises TelegramChannelClientError on failure.
        """
        try:
            from telegram.constants import ParseMode  # noqa: PLC0415

            bot = self._get_bot()
            msg = await bot.send_message(  # type: ignore[union-attr]
                chat_id=self._channel_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("Telegram channel message posted: message_id=%s", msg.message_id)
            return str(msg.message_id)

        except TelegramChannelClientError:
            raise
        except Exception as exc:
            # Map Telegram flood/rate errors to 429 so the circuit breaker recognises them
            msg_str = str(exc).lower()
            status = 429 if ("retry" in msg_str or "flood" in msg_str) else 500
            raise TelegramChannelClientError(str(exc), status_code=status) from exc
