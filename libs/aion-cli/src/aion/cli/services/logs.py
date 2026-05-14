"""Utilities for tailing authenticated Aion deployment version logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional


AionGqlContextClient = None


@dataclass(frozen=True)
class LogProperty:
    """A structured log property rendered alongside a log line when requested.

    Args:
        key: Property key.
        value: Property value.
    """

    key: str
    value: str


@dataclass(frozen=True)
class LogLine:
    """Normalized log event data used by the CLI renderer.

    Args:
        timestamp: Event timestamp.
        level: Log level name.
        message: Log message, if provided by the backend.
        properties: Structured key/value properties attached to the event.
    """

    timestamp: Any
    level: str
    message: Optional[str]
    properties: list[LogProperty]


def utc_now_rfc3339(clock: Callable[[], datetime] | None = None) -> str:
    """Return the current UTC time as an RFC 3339 timestamp.

    Args:
        clock: Optional callable used by tests to provide the current time.

    Returns:
        RFC 3339 UTC timestamp ending in ``Z``.
    """
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_since(value: Optional[str]) -> str:
    """Parse a CLI ``--since`` value into the backend timestamp string.

    Args:
        value: Optional RFC 3339/ISO-8601 timestamp supplied by the user.

    Returns:
        RFC 3339 timestamp string. Defaults to the current UTC time when
        ``value`` is ``None``.

    Raises:
        ValueError: If the supplied value cannot be parsed as a datetime.
    """
    if value is None:
        return utc_now_rfc3339()

    parse_value = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(parse_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_context_client():
    """Return the GraphQL context client class, importing it lazily."""
    global AionGqlContextClient
    if AionGqlContextClient is None:
        from aion.api.gql import AionGqlContextClient as resolved_client

        AionGqlContextClient = resolved_client
    return AionGqlContextClient


def _get_field(value: Any, field_name: str) -> Any:
    """Read a field from either a generated model object or a dictionary."""
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _normalize_properties(properties: Any) -> list[LogProperty]:
    """Normalize generated property objects into ``LogProperty`` records."""
    if not properties:
        return []

    normalized: list[LogProperty] = []
    for prop in properties:
        key = _get_field(prop, "key")
        value = _get_field(prop, "value")
        normalized.append(LogProperty(key=str(key), value=str(value)))
    return normalized


def normalize_log_payload(payload: Any) -> Optional[LogLine]:
    """Normalize a generated ``VersionLogs`` payload into a log line.

    Args:
        payload: Generated subscription payload or compatible dictionary.

    Returns:
        A normalized log line, or ``None`` when the subscription payload is
        empty.
    """
    event = _get_field(payload, "version_logs")
    if event is None and isinstance(payload, dict):
        event = payload.get("versionLogs")
    if event is None:
        return None

    return LogLine(
        timestamp=_get_field(event, "timestamp"),
        level=str(_get_field(event, "level") or ""),
        message=_get_field(event, "message"),
        properties=_normalize_properties(_get_field(event, "properties")),
    )


def _format_timestamp(value: Any) -> str:
    """Format a timestamp value for human-readable log output."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def format_log_line(log_line: LogLine, *, include_properties: bool = False) -> str:
    """Render one log event in a tail-friendly single-line format.

    Args:
        log_line: Normalized log event.
        include_properties: Whether to append structured properties.

    Returns:
        Rendered log line.
    """
    timestamp = _format_timestamp(log_line.timestamp)
    message = log_line.message or ""
    line = f"[{timestamp}] {log_line.level} {message}".rstrip()
    if include_properties and log_line.properties:
        properties = " ".join(
            f"{prop.key}={prop.value}" for prop in log_line.properties
        )
        line = f"{line} {properties}"
    return line


async def iter_version_log_lines(
    start_time: str,
    *,
    include_properties: bool = False,
) -> AsyncIterator[str]:
    """Yield formatted log lines from the version-authenticated subscription.

    Args:
        start_time: RFC 3339 lower bound for log retrieval.
        include_properties: Whether to append structured properties.

    Yields:
        Formatted log lines.
    """
    context_client = _resolve_context_client()
    async with context_client() as client:
        async for payload in client.version_logs(start_time=start_time):
            log_line = normalize_log_payload(payload)
            if log_line is not None:
                yield format_log_line(
                    log_line, include_properties=include_properties
                )


async def print_version_logs(
    start_time: str,
    *,
    include_properties: bool = False,
    write: Callable[[str], Any] = print,
) -> None:
    """Print version log events until the websocket subscription ends.

    Args:
        start_time: RFC 3339 lower bound for log retrieval.
        include_properties: Whether to append structured properties.
        write: Output callable used by tests and the CLI.
    """
    async for line in iter_version_log_lines(
        start_time, include_properties=include_properties
    ):
        write(line)
