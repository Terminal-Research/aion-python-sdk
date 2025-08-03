"""JWT management utilities for Aion API clients."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from .client import AionHttpClient

logger = logging.getLogger(__name__)


@dataclass
class Token:
    """
    Represents an authentication token with expiration metadata.

    This dataclass encapsulates a JWT token string along with its expiration
    timestamp, providing convenient methods for checking token validity
    and expiration status.

    Attributes:
        value (str): The JWT token string
        expires_at (datetime): Token expiration timestamp in UTC
    """

    value: str
    expires_at: datetime

    @classmethod
    def from_jwt(cls, token: str) -> "Token":
        """Create Token instance from JWT string by decoding expiration.

        Decodes the JWT token to extract the expiration timestamp and creates
        a Token instance with the original token string and parsed expiration time.

        Args:
            token (str): JWT token string to decode
        """
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        return cls(value=token, expires_at=exp)

    @property
    def expired(self) -> bool:
        """Check if the token has expired.

        Compares the current UTC time with the token's expiration timestamp
        to determine if the token is no longer valid.
        """
        return datetime.now(tz=timezone.utc) >= self.expires_at

    @property
    def expires_soon(self) -> bool:
        """Check if the token expires within 1 minute.

        Determines if the token will expire soon (within 1 minute) to allow
        for proactive token refresh before expiration.
        """
        return datetime.now(tz=timezone.utc) >= self.expires_at - timedelta(minutes=1)


class AionJWTManager(ABC):
    """
    Thread-safe JWT token manager without automatic refresh.

    This manager handles the lifecycle of JWT tokens including:
    - Thread-safe access using asyncio locks
    - Caching of valid tokens to minimize API calls

    The manager ensures that only one token refresh operation occurs at a time
    and provides both token strings and :class:`Token` objects to consumers.
    """

    def __init__(self) -> None:
        self._token: Optional[Token] = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> Optional[str]:
        """
        Get a valid token string, refreshing if necessary.

        Returns the cached JWT token string, automatically calling
        :meth:`_refresh_token` when the current token is missing, expired,
        or expiring soon. If refresh fails, ``None`` is returned. This method is
        thread-safe and ensures only one refresh operation occurs at a time.
        """
        async with self._lock:
            if self.should_refresh_token():
                with suppress(Exception):
                    await self._refresh_token()
            return None if not self._token else self._token.value

    def should_refresh_token(self) -> bool:
        """
        Check if the current token needs refresh.

        Determines whether a token refresh is needed based on:
        - No token exists (first time)
        - Current token has expired
        - Current token expires within 1 minute
        """
        if self._token is None:
            return True
        return self._token.expired or self._token.expires_soon

    @abstractmethod
    async def _refresh_token(self) -> None:
        """Refresh the token using custom logic."""

    def clear_token(self) -> None:
        """Clear the stored token from cache.

        Removes the currently cached token, forcing a fresh authentication
        on the next token request. Useful for logout scenarios or when
        switching between different authentication contexts.
        """
        self._token = None


class AionRefreshingJWTManager(AionJWTManager):
    """
    Thread-safe JWT token manager with automatic refresh capabilities.

    Integrates with :class:`AionHttpClient` for authentication.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = AionHttpClient()

    async def get_token(self) -> Optional[str]:
        """Return a valid token, refreshing it if necessary."""
        async with self._lock:
            if self.should_refresh_token():
                with suppress(Exception):
                    await self._refresh_token()
            return None if not self._token else self._token.value

    async def _refresh_token(self) -> None:
        """
        Refresh the token using the HTTP client.

        Performs the actual token refresh by:
        1. Calling the authentication endpoint via HTTP client
        2. Extracting the access token from the response
        3. Creating a new :class:`Token` object from the JWT
        4. Updating the cached token
        """
        first_token = self._token is None
        try:
            data = await self._client.authenticate()
            token_value = data.get("accessToken")
            if not token_value:
                raise ValueError("Access Token is missing in response.")

            self._token = Token.from_jwt(token_value)

            if first_token:
                processed_action = "fetched"
            else:
                processed_action = "refreshed"
            logger.info(
                "Token %s successfully, expires at: %s",
                processed_action,
                self._token.expires_at,
            )
        except Exception as ex:  # pragma: no cover - pass through to caller
            logger.error("Token refresh failed: %s", ex)
            raise


# Global instance used by the API client
aion_jwt_manager = AionRefreshingJWTManager()

