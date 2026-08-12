"""JWT management utilities for Aion API clients."""

from __future__ import annotations
import logging

import asyncio
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from aion.api.exceptions import AionAuthenticationError
from .client import AionHttpClient, auth_tokens_url

logger = logging.getLogger(__name__)

VERSION_SUBJECT_TYPE = "Version"
"""Value of the ``sub_type`` claim on a token issued to a deployment version.

A token minted from ``AION_CLIENT_ID``/``AION_CLIENT_SECRET`` is already scoped
to one version, and its ``sub`` is that version's id. Other principals - a user,
notably - authenticate against the same endpoint and put their own id in ``sub``,
so the type has to be checked before the subject is read as a version.
"""


def describe_exception(ex: BaseException) -> str:
    """Render an exception for a log line, keeping the type when the message is empty.

    httpx network failures - ConnectTimeout, ReadTimeout, ConnectError - carry an
    empty ``str()``. Formatting one with "%s" produces a line that trails off after
    the colon and names neither what failed nor why, which is exactly the case a
    reader of an authentication failure most needs to distinguish.
    """
    text = str(ex)
    return f"{type(ex).__name__}: {text}" if text else type(ex).__name__


def describe_lifetime(token: "Token") -> str:
    """Say how long a token is good for, in the reader's own clock.

    Expiry is held in UTC, while log timestamps are written in local time. A bare
    UTC expiry printed beside them reads as a token that died hours before it was
    issued, so it is converted here and led by the remaining lifetime, which is
    the part worth knowing at a glance.
    """
    remaining = int((token.expires_at - datetime.now(tz=timezone.utc)).total_seconds())
    lifetime = f"{remaining}s" if abs(remaining) < 120 else f"{remaining // 60}m"
    expires_at = token.expires_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"valid for {lifetime} (expires {expires_at})"


def describe_principal(token: "Token") -> str:
    """Say who a token authenticates as, for a line that explains a refusal.

    A version withheld from a caller is otherwise indistinguishable from one that
    was never issued: both surface as ``None``, and the claims that decided it are
    decoded and dropped in the same breath. Naming the principal turns "no version"
    into "not that kind of token", which is the difference between a wrong client
    id and an issuer that renamed a claim.
    """
    if token.subject_type is None:
        return "an unidentified principal (no sub_type claim)"
    return f"{token.subject_type} {token.subject}"


@dataclass
class Token:
    """
    Represents an authentication token with the metadata it carries.

    This dataclass encapsulates a JWT token string along with its expiration
    timestamp and the identity it authenticates as, providing convenient
    methods for checking token validity and expiration status.

    Identity is held twice on purpose. ``version_id`` is the fact consumers act
    on, already judged; ``subject`` and ``subject_type`` are what the token
    claimed, kept verbatim so a withheld version can be explained rather than
    just observed. Only the claim pair is a transcription, and only because
    something reads it: see :func:`describe_principal`.

    Attributes:
        value (str): The JWT token string
        expires_at (datetime): Token expiration timestamp in UTC
        version_id (Optional[str]): The deployment version this token
            authenticates as, or ``None`` for any other principal
        subject (Optional[str]): The ``sub`` claim as issued - the id of
            whatever principal this token belongs to
        subject_type (Optional[str]): The ``sub_type`` claim as issued - what
            kind of principal ``subject`` identifies
    """

    value: str
    expires_at: datetime
    version_id: Optional[str] = None
    subject: Optional[str] = None
    subject_type: Optional[str] = None

    @classmethod
    def from_jwt(cls, token: str) -> "Token":
        """Create Token instance from JWT string by decoding its claims.

        Decodes the JWT token to extract the expiration timestamp and the
        deployment version the token is scoped to, and creates a Token instance
        with the original token string alongside them.

        The version is withheld unless ``sub_type`` says the subject is one.
        Other principals - a user, notably - authenticate against the same
        endpoint and put their own id in the same claim, so reading ``sub``
        unguarded would hand back a user id for a version id and register a
        deployment under it.

        The signature is deliberately not verified. This token was just issued
        by the Aion auth endpoint over TLS and no local trust decision rests on
        it - the server validates it again on every request - and the SDK has no
        public key to verify it with.

        Args:
            token (str): JWT token string to decode
        """
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        subject = payload.get("sub")
        subject_type = payload.get("sub_type")
        return cls(
            value=token,
            expires_at=exp,
            version_id=subject if subject_type == VERSION_SUBJECT_TYPE else None,
            subject=subject,
            subject_type=subject_type,
        )

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
        self._last_auth_error: Optional[str] = None

    @property
    def last_auth_error(self) -> Optional[str]:
        """Describe the most recent authentication failure, or ``None`` after a success.

        A missing token is a soft failure here - callers get ``None`` rather than an
        exception - so the reason has to travel somewhere. Consumers that can only
        observe the absent token read this to report why it is absent.
        """
        return self._last_auth_error

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
                await self._try_refresh()
            return None if not self._token else self._token.value

    async def get_version_id(self) -> Optional[str]:
        """Return the deployment version the current token is scoped to.

        Credentials are issued per version, so the version id is already in the
        token and needs no separate lookup against the control plane. Returns
        ``None`` when no token could be obtained or when the token belongs to a
        principal that is not a version.

        Withholding is logged with the principal that caused it. This is the one
        place that still holds the claims behind the decision - the caller only
        sees ``None``, and by the time it falls back to the control plane there
        is nothing left to say why.

        The token is read back after :meth:`get_token` releases the lock; a
        refresh racing in between replaces it with one carrying the same
        subject, so the value cannot change underneath this call.
        """
        if await self.get_token() is None:
            return None
        if self._token is None:
            return None
        if self._token.version_id is None:
            logger.debug(
                "Access token authenticates as %s, not a deployment version",
                describe_principal(self._token))
        return self._token.version_id

    async def _try_refresh(self) -> None:
        """Refresh the token, recording the failure instead of propagating it.

        The exception must not escape - every caller treats a missing token as a
        soft failure - but discarding it silently is what turns a connect timeout
        into an unexplained "no token". Record it and log the traceback once, here,
        so no caller has to re-raise to find out what happened.
        """
        try:
            await self._refresh_token()
        except Exception as ex:
            self._record_auth_error(describe_exception(ex), exc_info=True)

    def _record_auth_error(
        self,
        reason: str,
        *,
        retryable: bool = False,
        exc_info: bool = False,
    ) -> None:
        """Store and log an authentication failure against the endpoint it came from.

        ``retryable`` downgrades the record to a warning and says so in the message:
        a 5xx resolves itself on the next request, while rejected credentials or an
        unexpected exception need someone to look.
        """
        self._last_auth_error = reason
        logger.log(
            logging.WARNING if retryable else logging.ERROR,
            "Aion authentication failed against %s - %s.%s",
            auth_tokens_url(),
            reason,
            " Refresh will be retried on the next token request." if retryable else "",
            exc_info=exc_info)

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
        self._auth_failed = False
        self._sync_lock = threading.Lock()

    async def get_token(self) -> Optional[str]:
        """
        Get a valid token string, refreshing if necessary.

        Returns the cached JWT token string, automatically calling
        :meth:`_refresh_token` when the current token is missing, expired,
        or expiring soon. If a previous 401 error occurred, returns None
        without attempting refresh. This method is thread-safe and ensures
        only one refresh operation occurs at a time.
        """
        async with self._lock:
            # If authentication failed previously, don't attempt refresh
            if self._auth_failed:
                return None

            if self.should_refresh_token():
                await self._try_refresh()
            return None if not self._token else self._token.value

    def _store_token(self, data: Dict[str, Any]) -> None:
        """
        Cache the token carried by an authentication response.

        Extracts the access token, creates a :class:`Token` from it, and clears
        the auth-failed latch so a recovered endpoint resumes serving tokens.
        """
        token_value = data.get("accessToken")
        if not token_value:
            raise ValueError("Access Token is missing in response.")

        first_token = self._token is None
        self._token = Token.from_jwt(token_value)
        # Reset auth failed flag on successful authentication
        self._auth_failed = False
        self._last_auth_error = None

        logger.info(
            "Token %s successfully, %s",
            "fetched" if first_token else "refreshed",
            describe_lifetime(self._token),
        )

    def _handle_auth_error(self, ex: AionAuthenticationError) -> None:
        """
        Drop the cached token, latching only when credentials were rejected.

        Only a 401 means the credentials themselves are wrong, which no amount
        of retrying will fix. Every other status - a 404 from a stale endpoint,
        a 5xx, a gateway error - is transient or a deployment problem, and
        latching on it would disable authentication for every consumer sharing
        this manager until the process restarts.
        """
        self._token = None

        if ex.status_code == 401:
            self._auth_failed = True
            self._record_auth_error(
                "401 - credentials rejected (check AION_CLIENT_ID / AION_CLIENT_SECRET "
                "against this host). Further refresh attempts will be skipped until reset")
            return

        self._record_auth_error(describe_exception(ex), retryable=True)

    async def _refresh_token(self) -> None:
        """
        Refresh the token using the HTTP client.

        Performs the actual token refresh by:
        1. Calling the authentication endpoint via HTTP client
        2. Extracting the access token from the response
        3. Creating a new :class:`Token` object from the JWT
        4. Updating the cached token

        If a 401 error occurs, sets the auth_failed flag to prevent
        further refresh attempts.
        """
        # If authentication failed previously, don't attempt refresh
        if self._auth_failed:
            return

        try:
            self._store_token(await self._client.authenticate())

        except AionAuthenticationError as ex:
            self._handle_auth_error(ex)

        # Anything else - a connect timeout, a DNS failure, missing credentials - is
        # recorded and logged with its traceback by the _try_refresh caller.

    def get_token_sync(self) -> Optional[str]:
        """
        Get a valid token string from synchronous code.

        This checks the same cached token used by async callers and refreshes
        it through the synchronous HTTP client when it is missing, expired, or
        expiring soon.
        """
        with self._sync_lock:
            if self._auth_failed:
                return None

            if self.should_refresh_token():
                self._try_refresh_sync()
            return None if not self._token else self._token.value

    def _try_refresh_sync(self) -> None:
        """Refresh synchronously, recording the failure instead of propagating it.

        The synchronous mirror of :meth:`_try_refresh`; see it for why the exception
        is absorbed here rather than by each caller.
        """
        try:
            self._refresh_token_sync()
        except Exception as ex:
            self._record_auth_error(describe_exception(ex), exc_info=True)

    def _refresh_token_sync(self) -> None:
        """
        Refresh the token through the synchronous HTTP client.

        This is used by OpenAI-compatible model clients that call API-key
        providers from synchronous request paths.
        """
        if self._auth_failed:
            return

        try:
            self._store_token(self._client.authenticate_sync())

        except AionAuthenticationError as ex:
            self._handle_auth_error(ex)

        # As in _refresh_token, unexpected failures are left to _try_refresh_sync.

    def reset_auth_state(self) -> None:
        """
        Reset the authentication failure state.

        Clears the auth_failed flag and cached token, allowing fresh
        authentication attempts. Use this when credentials have been
        updated or when you want to retry authentication after a 401 error.
        """
        self._auth_failed = False
        self._token = None
        self._last_auth_error = None
        logger.info("Authentication state reset. Fresh authentication will be attempted.")

    @property
    def is_auth_failed(self) -> bool:
        """
        Check if authentication has failed due to 401 error.

        Returns:
            bool: True if a 401 error occurred and no further refresh
                  attempts will be made, False otherwise.
        """
        return self._auth_failed


# Global instance used by the API client
aion_jwt_manager = AionRefreshingJWTManager()
