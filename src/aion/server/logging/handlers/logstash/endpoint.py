"""Resolution of the Logstash endpoint from environment configuration.

``LOGSTASH_HOST`` accepts either a full URL or a bare host, and everything
else -- scheme, port, path and whether to authenticate with an Aion platform
token -- is derived from it. This module holds that derivation so the parsing
rules live in one place instead of being spread across settings and setup,
and sits next to the handler and transport that consume the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

__all__ = ["LogstashEndpoint", "resolve_logstash_endpoint"]

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class LogstashEndpoint:
    """A fully resolved Logstash destination.

    Attributes:
        scheme: Either ``http`` or ``https``.
        host: Hostname of the Logstash endpoint.
        port: Resolved port, defaulting to the default port of the scheme.
        path: Path without the leading slash ('' for the root path).
        use_platform_auth: Whether to send an Aion platform Bearer token.
    """

    scheme: str
    host: str
    port: int
    path: str
    use_platform_auth: bool

    @property
    def url(self) -> str:
        """Return the endpoint URL as sent to."""
        return f"{self.scheme}://{self.host}:{self.port}/{self.path}"

    def describe(self) -> str:
        """Return a human readable summary for startup diagnostics."""
        auth = "enabled" if self.use_platform_auth else "disabled"
        return f"{self.url} (platform auth: {auth})"


def is_platform_host(host: str, platform_host: Optional[str]) -> bool:
    """Return True when ``host`` belongs to the Aion platform domain.

    The comparison is made against the registrable domain of the configured
    Aion API host, so that every environment (production, staging, ...) is
    recognised without hardcoding a domain.

    Matching is exact-or-subdomain on the parsed hostname. A substring check
    would be unsafe here: the platform token is a full client credential, and
    ``"aion.to" in host`` also matches unrelated domains such as
    ``aion.tools`` or ``aion.torture.com``.

    Args:
        host: Hostname of the Logstash endpoint.
        platform_host: Hostname of the Aion API (e.g. ``api.aion.to``).
    """
    if not host or not platform_host:
        return False

    domain = _registrable_domain(platform_host)
    if not domain:
        return False

    host = host.lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _registrable_domain(host: str) -> str:
    """Return the last two labels of a hostname ('api.aion.to' -> 'aion.to')."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) < 2:
        return ""
    return ".".join(labels[-2:])


def resolve_logstash_endpoint(
        raw_host: Optional[str],
        raw_port: Optional[int],
        platform_host: Optional[str],
) -> Optional[LogstashEndpoint]:
    """Resolve the Logstash endpoint, or None when logging is not configured.

    ``raw_host`` may be a full URL (``https://logs.aion.to/ingest``) or a bare
    host (``localhost``, ``logs.aion.to:5000``). The scheme defaults to HTTPS
    for Aion platform hosts and to HTTP otherwise; the port falls back to
    ``raw_port`` and then to the default port of the scheme.

    Args:
        raw_host: Value of LOGSTASH_HOST.
        raw_port: Value of LOGSTASH_PORT, used only when the host carries no port.
        platform_host: Hostname of the Aion API, used to detect platform endpoints.

    Raises:
        ValueError: If the host is malformed or uses an unsupported scheme.
    """
    if not raw_host:
        return None

    raw_host = raw_host.strip()
    if not raw_host:
        return None

    scheme, host, port, path = _split(raw_host)

    if not host:
        raise ValueError(f"LOGSTASH_HOST does not contain a hostname: {raw_host!r}")

    on_platform = is_platform_host(host, platform_host)
    if scheme is None:
        scheme = "https" if on_platform else "http"

    return LogstashEndpoint(
        scheme=scheme,
        host=host,
        port=port or raw_port or _DEFAULT_PORTS[scheme],
        path=path,
        # A bearer credential is never put on the wire in the clear, so a
        # platform host reached over plain HTTP gets no token.
        use_platform_auth=on_platform and scheme == "https",
    )


def _split(raw_host: str) -> tuple[Optional[str], Optional[str], Optional[int], str]:
    """Split LOGSTASH_HOST into (scheme, host, port, path).

    The scheme is None when the value is a bare host. Note that a bare host
    cannot be handed to urlsplit directly: it parses ``localhost:5000`` as
    scheme ``localhost`` with path ``5000``.
    """
    has_scheme = "://" in raw_host
    parsed = urlsplit(raw_host if has_scheme else f"//{raw_host}")

    scheme = parsed.scheme.lower() if has_scheme else None
    if scheme is not None and scheme not in _DEFAULT_PORTS:
        raise ValueError(
            f"LOGSTASH_HOST has an unsupported scheme {scheme!r}; "
            f"expected one of {sorted(_DEFAULT_PORTS)}")

    try:
        port = parsed.port
    except ValueError as ex:
        raise ValueError(f"LOGSTASH_HOST has an invalid port: {raw_host!r}") from ex

    return scheme, parsed.hostname, port, parsed.path.strip("/")
