"""Opaque keyset cursor for ``PostgresTaskStore.list``.

``a2a.utils.task.encode_page_token``/``decode_page_token`` only carry a task
id, which is why the store used to look the position back up with
``TasksRepository.sort_key_for_id`` on every paged request. This codec
carries the keyset position itself - ``status_timestamp`` and ``id`` - so a
page token decodes straight into the predicate ``find_page`` needs, and a
fingerprint of the request's filters so a token cannot be replayed against a
different query and silently return the wrong slice.

Not a security boundary: the token is for navigating a result set the caller
is already allowed to see, so it is Base64URL-encoded JSON without a
signature. Tampering with it can only move the cursor to another position of
the same reachable listing.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
from dataclasses import dataclass

from a2a.utils.errors import InvalidParamsError

__all__ = ["PageCursor", "encode_page_token", "decode_page_token"]

_VERSION = "v1"


@dataclass(frozen=True)
class PageCursor:
    """The keyset position a page token names."""

    status_timestamp: _dt.datetime
    id: str


def _filters_fingerprint(
    *,
    context_id: str | None,
    status_state: str | None,
    status_timestamp_after: _dt.datetime | None,
) -> dict:
    return {
        "context_id": context_id,
        "status_state": status_state,
        "status_timestamp_after": (
            status_timestamp_after.isoformat() if status_timestamp_after else None
        ),
    }


def encode_page_token(
    cursor: PageCursor,
    *,
    context_id: str | None,
    status_state: str | None,
    status_timestamp_after: _dt.datetime | None,
) -> str:
    """Encode a keyset position and the filters it is only valid under."""
    payload = {
        "status_timestamp": cursor.status_timestamp.isoformat(),
        "id": cursor.id,
        "filters": _filters_fingerprint(
            context_id=context_id,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        ),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
    return f"{_VERSION}.{encoded.decode('ascii').rstrip('=')}"


def decode_page_token(
    token: str,
    *,
    context_id: str | None,
    status_state: str | None,
    status_timestamp_after: _dt.datetime | None,
) -> tuple[_dt.datetime, str]:
    """Decode a page token into the ``(status_timestamp, id)`` it names.

    Raises:
        InvalidParamsError: If the token is not well-formed, names an
            unknown version, or was issued for different filters than the
            current request applies.
    """
    version, _, encoded = token.partition(".")
    if version != _VERSION or not encoded:
        raise InvalidParamsError(f"Invalid page token: {token}")

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidParamsError(f"Invalid page token: {token}")

    if not isinstance(payload, dict):
        raise InvalidParamsError(f"Invalid page token: {token}")

    expected_filters = _filters_fingerprint(
        context_id=context_id,
        status_state=status_state,
        status_timestamp_after=status_timestamp_after,
    )
    if payload.get("filters") != expected_filters:
        raise InvalidParamsError(f"Invalid page token: {token}")

    try:
        status_timestamp = _dt.datetime.fromisoformat(payload["status_timestamp"])
        cursor_id = payload["id"]
    except (KeyError, TypeError, ValueError):
        raise InvalidParamsError(f"Invalid page token: {token}")

    if not isinstance(cursor_id, str):
        raise InvalidParamsError(f"Invalid page token: {token}")

    return status_timestamp, cursor_id
