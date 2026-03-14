"""
api/services/twitter/client.py
-------------------------------
Twitter API v2 client abstraction.

Uses tweepy.AsyncClient for OAuth 1.0a User Context (posting tweets).
Wraps all calls so the rest of the module never imports tweepy directly.

If tweepy is not installed, the client degrades gracefully — all methods
raise TwitterClientError with a helpful message.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TwitterClientError(Exception):
    """Raised when a Twitter API call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class TwitterClient:
    """
    Thin wrapper around the Twitter API v2 for posting tweets.

    Usage
    -----
        client = TwitterClient(api_key=..., ...)
        tweet_id = await client.post_tweet("Hello world")
        reply_id = await client.post_tweet("Reply", reply_to=tweet_id)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
        bearer_token: str = "",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token
        self._access_token_secret = access_token_secret
        self._bearer_token = bearer_token
        self._client: object | None = None

    async def _ensure_client(self) -> None:
        """Lazily initialize the tweepy async client."""
        if self._client is not None:
            return

        try:
            import tweepy  # noqa: PLC0415
        except ImportError as exc:
            raise TwitterClientError(
                "tweepy is not installed. Run: pip install tweepy[async]"
            ) from exc

        self._client = tweepy.AsyncClient(
            consumer_key=self._api_key,
            consumer_secret=self._api_secret,
            access_token=self._access_token,
            access_token_secret=self._access_token_secret,
            bearer_token=self._bearer_token or None,
            wait_on_rate_limit=False,
        )
        logger.info("Twitter API client initialized")

    async def post_tweet(self, text: str, reply_to: str | None = None) -> str:
        """
        Post a tweet.  Returns the tweet ID as a string.

        Raises TwitterClientError on failure (with status_code for
        the circuit breaker to inspect).
        """
        await self._ensure_client()

        import tweepy  # noqa: PLC0415

        try:
            kwargs: dict = {"text": text}
            if reply_to:
                kwargs["in_reply_to_tweet_id"] = reply_to

            response = await self._client.create_tweet(**kwargs)  # type: ignore[union-attr]
            tweet_id = str(response.data["id"])
            logger.info("Tweet posted: %s", tweet_id)
            return tweet_id

        except tweepy.TweepyException as exc:
            status = 0
            if hasattr(exc, "response") and exc.response is not None:
                status = exc.response.status_code
            raise TwitterClientError(str(exc), status_code=status) from exc

    async def delete_tweet(self, tweet_id: str) -> None:
        """Delete a tweet by ID."""
        await self._ensure_client()

        import tweepy  # noqa: PLC0415

        try:
            await self._client.delete_tweet(tweet_id)  # type: ignore[union-attr]
            logger.info("Tweet deleted: %s", tweet_id)
        except tweepy.TweepyException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            raise TwitterClientError(str(exc), status_code=status) from exc

    async def get_tweet_metrics(self, tweet_id: str) -> dict:
        """
        Fetch engagement metrics for a tweet.

        Returns dict with keys: likes, retweets, replies, impressions.
        Requires elevated API access for impressions.
        """
        await self._ensure_client()

        import tweepy  # noqa: PLC0415

        try:
            response = await self._client.get_tweet(  # type: ignore[union-attr]
                tweet_id,
                tweet_fields=["public_metrics"],
            )
            metrics = response.data.get("public_metrics", {}) if response.data else {}
            return {
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
            }
        except tweepy.TweepyException as exc:
            logger.warning("Failed to fetch metrics for tweet %s: %s", tweet_id, exc)
            return {"likes": 0, "retweets": 0, "replies": 0, "impressions": 0}
