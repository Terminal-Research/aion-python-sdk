"""JWT management utilities for Aion API clients."""

from __future__ import annotations
import logging

import asyncio
import threading
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from aion.api.exceptions import AionAuthenticationError
from aion.core.settings import api_settings

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


class AionAuthState:
    """
    Process-wide latch for whether Aion authentication is worth attempting.

    Authentication is an all-or-nothing property of a process: once the platform
    rejects the configured credentials, every consumer in that process is
    equally dead, and once credentials are absent no consumer can do anything
    about it. Keeping that decision here means it is reported once — on the
    transition — instead of once per call site, and that the async and sync
    refresh paths share a single guard rather than racing two separate locks.

    Agent processes are forked from the CLI parent, so a child inherits this
    latch as-is: whatever the parent already discovered, the child does not
    rediscover and re-report.
    """

    def __init__(self) -> None:
        self._failed = False
        self._reported = False
        self._lock = threading.Lock()

    @property
    def failed(self) -> bool:
        """Whether a 401 has latched authentication off for this process."""
        return self._failed

    @property
    def can_attempt(self) -> bool:
        """Whether a token refresh is worth making right now."""
        if not api_settings.auth_configured:
            self._report_unconfigured()
            return False
        return not self._failed

    def mark_failed(self) -> None:
        """Latch authentication off after a 401, reporting only the transition."""
        with self._lock:
            first_report = not self._reported
            self._failed = True
            self._reported = True

        if first_report:
            logger.warning(
                "Aion authentication failed with 401 error. "
                "Further refresh attempts will be skipped until reset.")
        else:
            logger.debug("Aion authentication still failing with 401 error; refresh skipped.")

    def mark_succeeded(self) -> None:
        """Clear the latch so a later failure is reported again."""
        with self._lock:
            self._failed = False
            self._reported = False

    def reset(self) -> None:
        """Forget both the failure and the fact that it was reported."""
        self.mark_succeeded()

    def _report_unconfigured(self) -> None:
        """Explain once that authentication is unconfigured, not broken."""
        with self._lock:
            if self._reported:
                return
            self._reported = True

        logger.info(
            "Aion authentication is not configured "
            "(AION_CLIENT_ID/AION_CLIENT_SECRET are not set). "
            "Platform features are unavailable."
        )


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
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock_guard = threading.Lock()

    def _loop_lock(self) -> asyncio.Lock:
        """Return an ``asyncio.Lock`` owned by the currently running loop.

        This manager is a module-level singleton built before any loop exists,
        and agent processes are forked from the CLI parent, so one instance
        outlives several event loops. An ``asyncio.Lock`` binds to the first
        loop that contends for it and raises in every other one, so the lock is
        rebuilt whenever the running loop changes. Waiters are only discarded
        when the loop they belonged to is already gone.
        """
        loop = asyncio.get_running_loop()
        with self._lock_guard:
            if self._lock_loop is not loop:
                self._lock = asyncio.Lock()
                self._lock_loop = loop
            return self._lock

    async def get_token(self) -> Optional[str]:
        """
        Get a valid token string, refreshing if necessary.

        Returns the cached JWT token string, automatically calling
        :meth:`_refresh_token` when the current token is missing, expired,
        or expiring soon. If refresh fails, ``None`` is returned. This method is
        thread-safe and ensures only one refresh operation occurs at a time.
        """
        async with self._loop_lock():
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
        self._auth_state = AionAuthState()
        self._sync_lock = threading.Lock()

    async def get_token(self) -> Optional[str]:
        """
        Get a valid token string, refreshing if necessary.

        Returns the cached JWT token string, automatically calling
        :meth:`_refresh_token` when the current token is missing, expired,
        or expiring soon. If authentication is disabled or a previous 401 error
        occurred, returns None without attempting refresh. This method is
        thread-safe and ensures only one refresh operation occurs at a time.
        """
        async with self._loop_lock():
            # Authentication is off for this process — disabled, unconfigured,
            # or already rejected. Nothing to refresh.
            if not self._auth_state.can_attempt:
                return None

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

        If a 401 error occurs, latches authentication off in
        :class:`AionAuthState` to prevent further refresh attempts.
        """
        if not self._auth_state.can_attempt:
            return

        first_token = self._token is None
        try:
            data = await self._client.authenticate()
            token_value = data.get("accessToken")
            if not token_value:
                raise ValueError("Access Token is missing in response.")

            self._token = Token.from_jwt(token_value)
            self._auth_state.mark_succeeded()

            if first_token:
                processed_action = "fetched"
            else:
                processed_action = "refreshed"
            logger.info(
                "Token %s successfully, expires at: %s",
                processed_action,
                self._token.expires_at,
            )

        except AionAuthenticationError:
            self._token = None
            self._auth_state.mark_failed()

        except Exception as ex:
            logger.error("Token refresh failed: %s", ex)
            raise

    def get_token_sync(self) -> Optional[str]:
        """
        Get a valid token string from synchronous code.

        This checks the same cached token used by async callers and refreshes
        it through the synchronous HTTP client when it is missing, expired, or
        expiring soon. The auth state is shared with the async path, so a 401
        seen by either one stops the other from retrying and re-reporting it.
        """
        with self._sync_lock:
            if not self._auth_state.can_attempt:
                return None

            if self.should_refresh_token():
                with suppress(Exception):
                    self._refresh_token_sync()
            return None if not self._token else self._token.value

    def _refresh_token_sync(self) -> None:
        """
        Refresh the token through the synchronous HTTP client.

        This is used by OpenAI-compatible model clients that call API-key
        providers from synchronous request paths.
        """
        if not self._auth_state.can_attempt:
            return

        first_token = self._token is None
        try:
            data = self._client.authenticate_sync()
            token_value = data.get("accessToken")
            if not token_value:
                raise ValueError("Access Token is missing in response.")

            self._token = Token.from_jwt(token_value)
            self._auth_state.mark_succeeded()

            if first_token:
                processed_action = "fetched"
            else:
                processed_action = "refreshed"
            logger.info(
                "Token %s successfully, expires at: %s",
                processed_action,
                self._token.expires_at,
            )

        except AionAuthenticationError:
            self._token = None
            self._auth_state.mark_failed()

        except Exception as ex:
            logger.error("Token refresh failed: %s", ex)
            raise

    def reset_auth_state(self) -> None:
        """
        Reset the authentication failure state.

        Clears the failure latch and cached token, allowing fresh
        authentication attempts. Use this when credentials have been
        updated or when you want to retry authentication after a 401 error.
        """
        self._auth_state.reset()
        self._token = None
        logger.info("Authentication state reset. Fresh authentication will be attempted.")

    @property
    def is_auth_failed(self) -> bool:
        """
        Check if authentication has failed due to 401 error.

        Returns:
            bool: True if a 401 error occurred and no further refresh
                  attempts will be made, False otherwise.
        """
        return self._auth_state.failed


# Global instance used by the API client
aion_jwt_manager = AionRefreshingJWTManager()
