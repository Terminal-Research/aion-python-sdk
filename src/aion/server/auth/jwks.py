"""The control plane's published keys, which anonymous session tokens are verified with."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

__all__ = [
    "JwksKeySource",
    "REFRESH_AFTER_SECONDS",
    "REFETCH_INTERVAL_SECONDS",
    "MISSED_REFRESHES_ALLOWED",
    "MAX_KEY_SET_AGE_SECONDS",
]

REFRESH_AFTER_SECONDS = 60
"""Age after which requests refresh the set to pick up key removals.

A key removed from the published JWKS remains accepted until a successful
refresh replaces the held set or the set exceeds ``MAX_KEY_SET_AGE_SECONDS``.
This threshold exceeds
``REFETCH_INTERVAL_SECONDS`` so age-triggered refreshes are eligible under
the fetch rate limit.
"""

REFETCH_INTERVAL_SECONDS = 10
"""Least time between fetches a request may cause - for an unknown ``kid`` or a stale set - so forged ``kid`` values or a control plane that keeps failing cannot turn every request into a fetch and a warning."""

MISSED_REFRESHES_ALLOWED = 3
"""Number of refresh intervals a held key set may remain unconfirmed."""

MAX_KEY_SET_AGE_SECONDS = MISSED_REFRESHES_ALLOWED * REFRESH_AFTER_SECONDS
"""A set not successfully refreshed for longer than this age is not used.

Deriving the age from ``REFRESH_AFTER_SECONDS`` preserves the rule of
``MISSED_REFRESHES_ALLOWED`` missed refreshes when the refresh interval changes.
"""

_FETCH_TIMEOUT_SECONDS = 10.0


class JwksKeySource:
    """A JWKS document fetched from a URL and kept in memory.

    ``load`` fetches it once at startup; a failure is logged and leaves the
    set empty, so tokens are refused until a later fetch succeeds. A failed
    refresh keeps the held keys until their age since the last successful
    fetch exceeds ``MAX_KEY_SET_AGE_SECONDS``.

    ``key_for`` answers from the held set. A set older than
    ``REFRESH_AFTER_SECONDS`` is refreshed in the background while the current
    request is still verified with what is held. A ``kid`` the set does not
    contain triggers an immediate fetch - the control plane may have rotated
    keys. Either way, requests cause at most one fetch per
    ``REFETCH_INTERVAL_SECONDS``, so a failing refresh is retried, and
    warned about, no more often than that. Fetches are single-flight:
    concurrent callers share one request.

    A request for a set older than ``MAX_KEY_SET_AGE_SECONDS`` waits for a
    permitted fetch before using any key. If the fetch fails or is rate-limited,
    the set is cleared and anonymous session tokens are refused until a fetch
    succeeds. Clearing logs one error; recovery logs one informational message.
    Requests drive refreshes; there is no background timer.
    """

    def __init__(
            self,
            url: str,
            *,
            client: Optional[httpx.AsyncClient] = None,
            clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt_at: Optional[float] = None
        self._inflight: Optional[asyncio.Task[None]] = None
        self._verification_suspended = False

    async def load(self) -> None:
        """Fetch the key set; a failure is logged and does not stop the server."""
        await self._refresh()

    async def key_for(self, kid: str) -> Optional[jwt.PyJWK]:
        """The key published under ``kid``, or ``None`` when there is none."""
        if self._keys and self._is_too_old():
            if self._inflight is not None or self._may_refetch():
                await self._refresh()
            if self._keys and self._is_too_old():
                self._keys.clear()
                self._verification_suspended = True
                logger.error(
                    "Authentication: the Aion verification keys have not been refreshed for more than %s seconds; "
                    "anonymous session tokens are refused until contact with the control plane is restored",
                    MAX_KEY_SET_AGE_SECONDS,
                )
            return self._keys.get(kid)
        key = self._keys.get(kid)
        if key is not None:
            if self._is_stale() and self._inflight is None and self._may_refetch():
                self._start_refresh()
            return key
        if self._inflight is not None or self._may_refetch():
            await self._refresh()
        return self._keys.get(kid)

    async def aclose(self) -> None:
        """Stop any fetch in flight and close the HTTP client this source opened."""
        if self._inflight is not None:
            self._inflight.cancel()
        if self._owns_client:
            await self._client.aclose()

    def _is_stale(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at > REFRESH_AFTER_SECONDS

    def _is_too_old(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at > MAX_KEY_SET_AGE_SECONDS

    def _may_refetch(self) -> bool:
        return (
            self._last_attempt_at is None
            or self._clock() - self._last_attempt_at >= REFETCH_INTERVAL_SECONDS
        )

    def _start_refresh(self) -> asyncio.Task[None]:
        if self._inflight is None:
            self._last_attempt_at = self._clock()
            self._inflight = asyncio.get_running_loop().create_task(self._fetch())
        return self._inflight

    async def _refresh(self) -> None:
        # Shielded: a caller that goes away must not cancel the fetch the
        # others are waiting on.
        await asyncio.shield(self._start_refresh())

    async def _fetch(self) -> None:
        try:
            response = await self._client.get(self._url)
            response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(response.json())
        except Exception as error:
            logger.warning(
                "Could not fetch the Aion verification keys from %s (%s: %s); %s",
                self._url,
                type(error).__name__,
                error,
                "keeping the keys already held" if self._keys else "anonymous session tokens are refused until they load",
            )
        else:
            self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
            self._fetched_at = self._clock()
            if self._verification_suspended:
                self._verification_suspended = False
                logger.info(
                    "Authentication: contact with the control plane is restored; "
                    "anonymous session token verification has resumed"
                )
        finally:
            self._inflight = None
