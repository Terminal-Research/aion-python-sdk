"""The A2A shape of an executable extension's error: how its failed task says why it failed.

A task of an executable extension that fails closes with a final status whose
message ends with two parts: the error's message as text, for a person, and
``{code, message, retryable}`` as a data part whose Event schema is
``{uri}#ExtensionError``, for a program. An executor writes it for its own
failures, and the server writes it the same way when it fails or settles such
a task itself.
"""

from __future__ import annotations

from a2a.types import Part
from google.protobuf import json_format, struct_pb2

from aion.core.a2a.extensions.event import EventPartMetadataV1
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1

__all__ = ["ERROR_SCHEMA_NAME", "error_parts", "error_schema"]

ERROR_SCHEMA_NAME = "ExtensionError"
"""Name of the error payload's shape, after the extension's URI: ``{uri}#ExtensionError``."""


def error_schema(uri: str) -> str:
    """The schema of the error payload a failed task of the extension at ``uri`` closes with."""
    return f"{uri}#{ERROR_SCHEMA_NAME}"


def error_parts(uri: str, code: str, text: str, retryable: bool) -> list[Part]:
    """The two parts that carry an extension's error: ``text`` for a person, the data for a program.

    Args:
        uri: The extension's URI.
        code: The error's code: the extension's own, or one of
            ``aion.core.a2a.codes``.
        text: The error's message, as it is to be shown.
        retryable: Whether repeating the request can succeed.
    """
    data = struct_pb2.Value()
    json_format.ParseDict({"code": code, "message": text, "retryable": retryable}, data)
    schema = EventPartMetadataV1(schema=error_schema(uri)).model_dump(by_alias=True)
    metadata = struct_pb2.Struct()
    json_format.ParseDict({EVENT_EXTENSION_URI_V1: schema}, metadata)
    return [Part(text=text), Part(data=data, media_type="application/json", metadata=metadata)]
