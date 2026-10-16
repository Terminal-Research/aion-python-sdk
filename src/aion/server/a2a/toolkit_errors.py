"""The A2A shape of a toolkit's error: how a failed task of a toolkit says why it failed.

A toolkit's turn that fails closes its task with a final status whose message
ends with two parts: the error's message as text, for a person, and
``{code, message, retryable}`` as a data part whose Event schema is
``{uri}#ToolkitError``, for a program. The toolkit host writes it for a turn's
result, and the server writes it the same way when it settles a toolkit's task
itself.
"""

from __future__ import annotations

from a2a.types import Part
from google.protobuf import json_format, struct_pb2

from aion.core.a2a.extensions.event import EventPartMetadataV1
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1

__all__ = ["ERROR_SCHEMA_NAME", "error_parts", "error_schema"]

ERROR_SCHEMA_NAME = "ToolkitError"
"""Name of the error payload's shape, after the toolkit's URI: ``{uri}#ToolkitError``."""


def error_schema(uri: str) -> str:
    """The schema of the error payload a failed turn of the toolkit at ``uri`` closes with."""
    return f"{uri}#{ERROR_SCHEMA_NAME}"


def error_parts(uri: str, code: str, text: str, retryable: bool) -> list[Part]:
    """The two parts that carry a toolkit's error: ``text`` for a person, the data for a program.

    Args:
        uri: The toolkit's extension URI.
        code: The error's code.
        text: The error's message, as it is to be shown.
        retryable: Whether repeating the request can succeed.
    """
    data = struct_pb2.Value()
    json_format.ParseDict({"code": code, "message": text, "retryable": retryable}, data)
    schema = EventPartMetadataV1(schema=error_schema(uri)).model_dump(by_alias=True)
    metadata = struct_pb2.Struct()
    json_format.ParseDict({EVENT_EXTENSION_URI_V1: schema}, metadata)
    return [Part(text=text), Part(data=data, media_type="application/json", metadata=metadata)]
