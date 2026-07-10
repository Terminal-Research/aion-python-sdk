from __future__ import annotations

from typing import Any

from google.protobuf.json_format import MessageToDict

__all__ = ["proto_to_dict"]


def proto_to_dict(value: Any) -> dict:
    """Convert a protobuf message or an already-deserialized dict to a plain dict.

    The A2A library may deliver metadata values as either protobuf Struct objects
    or plain Python dicts depending on the transport layer. This normalizes both.
    """
    if isinstance(value, dict):
        return value
    return MessageToDict(value)
