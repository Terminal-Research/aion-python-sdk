"""Aion's published verification keys, which every request token is verified with."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx
import jwt

logger = logging.getLogger(__name__)

__all__ = [
    "COLD_RETRY_SECONDS",
    "FETCH_DEADLINE_SECONDS",
    "JwksKeySource",
    "MAX_DOCUMENT_BYTES",
    "jwk_thumbprint",
]

FETCH_DEADLINE_SECONDS = 5.0
"""The whole fetch - connecting, sending and reading the document - has to finish within this."""

MAX_DOCUMENT_BYTES = 64 * 1024
"""A key set document longer than this is refused unread."""

COLD_RETRY_SECONDS = 1.0
"""Least time between fetches while no key is loaded, so requests cannot turn an outage into a fetch each."""

_LOOPBACK_NAMES = frozenset({"localhost"})


class JwksKeySource:
    """Aion's public JWKS, fetched from the configured API host and kept for the life of the process.

    ``load`` fetches it once at startup; a failure is logged and leaves no key,
    so protected requests are refused until a fetch succeeds. While there is no
    key, a request may start a fetch, at most one every
    ``COLD_RETRY_SECONDS``, and concurrent requests share it. Once keys are
    loaded they are kept until the process ends: there is no timer, no
    refresh, and a ``kid`` that is not among them is refused without a fetch.

    Only keys Aion signs request tokens with are kept: EC on P-256, for ES256,
    with no private part, and a ``kid`` that is the key's RFC 7638 thumbprint.
    A ``kid`` that appears more than once is ambiguous and kept for none.
    The URL has to be HTTPS unless it is a loopback address for development;
    redirects are not followed.
    """

    def __init__(
            self,
            url: str,
            *,
            client: Optional[httpx.AsyncClient] = None,
            clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _require_trusted_url(url)
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=FETCH_DEADLINE_SECONDS, follow_redirects=False)
        self._owns_client = client is None
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._last_attempt_at: Optional[float] = None
        self._inflight: Optional[asyncio.Task[None]] = None
        self._failing = False

    @property
    def url(self) -> str:
        return self._url

    @property
    def available(self) -> bool:
        """Whether keys are loaded; until they are, no token can be verified."""
        return bool(self._keys)

    async def load(self) -> None:
        """Fetch the key set; a failure is logged and does not stop the server."""
        await self._fetch_shared()

    async def key_for(self, kid: str) -> Optional[jwt.PyJWK]:
        """The key published under ``kid``, or ``None`` when there is none."""
        if not self._keys and (self._inflight is not None or self._may_retry()):
            await self._fetch_shared()
        return self._keys.get(kid)

    async def aclose(self) -> None:
        """Stop any fetch in flight and close the HTTP client this source opened."""
        if self._inflight is not None:
            self._inflight.cancel()
        if self._owns_client:
            await self._client.aclose()

    def _may_retry(self) -> bool:
        return self._last_attempt_at is None or self._clock() - self._last_attempt_at >= COLD_RETRY_SECONDS

    async def _fetch_shared(self) -> None:
        if self._inflight is None:
            self._last_attempt_at = self._clock()
            self._inflight = asyncio.get_running_loop().create_task(self._fetch())
        # Shielded: a caller that goes away must not cancel the fetch the
        # others are waiting on.
        await asyncio.shield(self._inflight)

    async def _fetch(self) -> None:
        try:
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                document = await self._read_document()
            keys = _usable_keys(document)
            if not keys:
                raise ValueError("the key set holds no usable ES256 key")
        except Exception as error:
            log = logger.debug if self._failing else logger.warning
            self._failing = True
            log(
                "Authentication: could not fetch the Aion verification keys from %s (%s: %s); "
                "protected requests are refused until they load",
                self._url,
                type(error).__name__,
                error,
            )
        else:
            self._keys = keys
            logger.info("Authentication: loaded the Aion verification keys from %s", self._url)
        finally:
            self._inflight = None

    async def _read_document(self) -> Any:
        async with self._client.stream("GET", self._url, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f"the key endpoint answered {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_DOCUMENT_BYTES:
                    raise ValueError(f"the key set is larger than {MAX_DOCUMENT_BYTES} bytes")
        return json.loads(bytes(body), object_pairs_hook=_no_duplicate_members)


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """The RFC 7638 SHA-256 thumbprint of an EC public JWK, unpadded base64url."""
    members = {name: jwk[name] for name in ("crv", "kty", "x", "y")}
    canonical = json.dumps(members, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).rstrip(b"=").decode("ascii")


def _usable_keys(document: Any) -> dict[str, jwt.PyJWK]:
    """The ES256 verification keys of a JWKS document, by ``kid``; ambiguous ``kid`` values dropped."""
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("the document is not a JWKS")
    keys: dict[str, jwt.PyJWK] = {}
    ambiguous: set[str] = set()
    for jwk in document["keys"]:
        kid = jwk.get("kid") if isinstance(jwk, dict) else None
        if not _is_es256_public_key(jwk):
            continue
        if kid in keys or kid in ambiguous:
            keys.pop(kid, None)
            ambiguous.add(kid)
            logger.warning("Authentication: the key id %s appears more than once in the key set; not used", kid)
            continue
        try:
            keys[kid] = jwt.PyJWK(jwk, algorithm="ES256")
        except jwt.PyJWTError:
            continue
    return keys


def _is_es256_public_key(jwk: Any) -> bool:
    if not isinstance(jwk, dict) or "d" in jwk:
        return False
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return False
    if jwk.get("alg", "ES256") != "ES256" or jwk.get("use", "sig") != "sig":
        return False
    if not all(isinstance(jwk.get(name), str) and jwk[name] for name in ("kid", "x", "y")):
        return False
    return jwk["kid"] == jwk_thumbprint(jwk)


def _no_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    members: dict[str, Any] = {}
    for name, value in pairs:
        if name in members:
            raise ValueError(f"the member '{name}' appears more than once")
        members[name] = value
    return members


def _require_trusted_url(url: str) -> None:
    """Keys come over HTTPS, or plain HTTP to this machine only, for development."""
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and _is_loopback(parsed.hostname):
        return
    raise ValueError(
        f"the verification keys URL {url!r} has to be HTTPS, or HTTP to a loopback address; check AION_API_HOST"
    )


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    if host.lower() in _LOOPBACK_NAMES or host.lower().endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
