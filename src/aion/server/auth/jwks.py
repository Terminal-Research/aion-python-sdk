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
]

REFRESH_AFTER_SECONDS = 600
"""Age after which a key set is refreshed in the background; the control plane rotates keys slowly."""

REFETCH_INTERVAL_SECONDS = 30
"""Least time between fetches a request may cause - for an unknown ``kid`` or a stale set - so forged ``kid`` values or a control plane that keeps failing cannot turn every request into a fetch and a warning."""

_FETCH_TIMEOUT_SECONDS = 10.0


class JwksKeySource:
    """A JWKS document fetched from a URL and kept in memory.

    ``load`` fetches it once at startup; a failure is logged and leaves the
    set empty, so tokens are refused until a later fetch succeeds. After that
    the set is never dropped: a failed refresh keeps the keys already held,
    because a stale key still verifies the tokens it signed.

    ``key_for`` answers from the held set. A set older than
    ``REFRESH_AFTER_SECONDS`` is refreshed in the background while the current
    request is still verified with what is held. A ``kid`` the set does not
    contain triggers an immediate fetch - the control plane may have rotated
    keys. Either way, requests cause at most one fetch per
    ``REFETCH_INTERVAL_SECONDS``, so a failing refresh is retried, and
    warned about, no more often than that. Fetches
    are single-flight: concurrent callers share one request.
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

    async def load(self) -> None:
        """Fetch the key set; a failure is logged and does not stop the server."""
        await self._refresh()

    async def key_for(self, kid: str) -> Optional[jwt.PyJWK]:
        """The key published under ``kid``, or ``None`` when there is none."""
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
        finally:
            self._inflight = None
