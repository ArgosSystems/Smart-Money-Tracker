"""
api/services/bluesky/client.py
--------------------------------
Bluesky (AT Protocol) client.

Uses the `atproto` library (synchronous) wrapped in asyncio.to_thread()
so it doesn't block the event loop — same pattern as TwitterClient/tweepy.

Install:  pip install atproto

Authentication uses an app password (Settings → App Passwords in Bluesky).
The client re-authenticates automatically if the session expires.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class BlueSkyClientError(Exception):
    """Raised when a Bluesky API call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class BlueSkyClient:
    """
    Posts text to Bluesky via the AT Protocol (atproto library).

    Usage
    -----
        client = BlueSkyClient(handle="smartmoney.bsky.social", password="xxxx-xxxx-xxxx")
        uri, cid = await client.post("🐋 Whale Alert on Ethereum…")
    """

    def __init__(self, handle: str, password: str) -> None:
        self._handle = handle
        self._password = password
        self._client: object | None = None

    def _get_client(self) -> object:
        """Return (or lazily create and authenticate) the atproto Client."""
        if self._client is not None:
            return self._client
        try:
            from atproto import Client  # noqa: PLC0415
        except ImportError as exc:
            raise BlueSkyClientError(
                "atproto is not installed. Run: pip install atproto"
            ) from exc

        client = Client()
        client.login(self._handle, self._password)
        self._client = client
        logger.info("BlueSkyClient authenticated as @%s", self._handle)
        return self._client

    async def post(self, text: str) -> tuple[str, str]:
        """
        Post a text message to Bluesky.

        Returns (uri, cid) — the AT URI and content ID of the new post.
        Raises BlueSkyClientError on failure.
        """

        def _do_post() -> tuple[str, str]:
            try:
                client = self._get_client()
                response = client.send_post(text=text)  # type: ignore[union-attr]
                logger.info("Bluesky post created: %s", response.uri)
                return response.uri, response.cid
            except BlueSkyClientError:
                raise
            except Exception as exc:
                # atproto raises atproto_core.exceptions.AtProtocolError (has status_code)
                status = getattr(exc, "status_code", 0) or 0
                if status == 0:
                    msg_lower = str(exc).lower()
                    if "rate" in msg_lower or "limit" in msg_lower:
                        status = 429
                    elif "auth" in msg_lower or "session" in msg_lower:
                        # Session expired — clear cached client so next call re-authenticates
                        self._client = None
                        status = 401
                    else:
                        status = 500
                raise BlueSkyClientError(str(exc), status_code=status) from exc

        return await asyncio.to_thread(_do_post)
