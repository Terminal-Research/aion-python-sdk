"""Encoding protobuf values for JSONB bind parameters in raw SQL.

The ORM columns carry a type decorator that does this on the way in. Raw SQL
bypasses it, so the two writers that fence their statements by hand — the task
upsert and the cancellation — would each grow their own conversion. They share
this one instead, so a task written by either lands in the same shape.
"""

from __future__ import annotations

import json

from google.protobuf import json_format

__all__ = ["as_jsonb", "repeated_as_jsonb"]


def as_jsonb(message) -> str | None:
    """Encode one protobuf message as a JSONB bind, or SQL NULL if absent.

    Args:
        message: Protobuf message to encode, or ``None``.

    Returns:
        A JSON string, or ``None`` so the bind becomes SQL NULL rather than
        the JSON literal ``null``.
    """
    if message is None:
        return None
    return json.dumps(json_format.MessageToDict(message))


def repeated_as_jsonb(messages) -> str | None:
    """Encode a nullable repeated protobuf field as a JSONB array bind.

    Args:
        messages: Iterable of protobuf messages, or ``None``.

    Returns:
        A JSON array string, or ``None`` for an absent field.
    """
    if messages is None:
        return None
    return json.dumps([json_format.MessageToDict(message) for message in messages])
