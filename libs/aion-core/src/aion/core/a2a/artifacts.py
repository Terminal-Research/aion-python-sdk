"""Framework-agnostic artifact builders.

Constructs a2a.types.Artifact objects without any framework-specific details.
Used directly in Thread.post() and in emit_artifact() helpers across frameworks.
"""

from __future__ import annotations

from uuid import uuid4

from a2a.types import Artifact, Part
from google.protobuf import json_format, struct_pb2


def file_artifact(
    *,
    url: str | None = None,
    data: bytes | None = None,
    mime_type: str,
    name: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Build an Artifact carrying a single file part.

    Exactly one of url or data must be provided.

    Args:
        url: File URL (FileWithUri). Mutually exclusive with data.
        data: File content as bytes (FileWithBytes). Mutually exclusive with url.
        mime_type: MIME type of the file (e.g. "image/png", "application/pdf").
        name: Human-readable artifact name. Defaults to "file".
        artifact_id: Explicit artifact ID. Auto-generated if not provided.

    Returns:
        a2a.types.Artifact with a single FilePart.

    Raises:
        ValueError: If neither or both of url/data are provided.
        TypeError: If data is not bytes.
    """
    if url is None and data is None:
        raise ValueError("Exactly one of 'url' or 'data' must be provided")
    if url is not None and data is not None:
        raise ValueError("Provide either 'url' or 'data', not both")
    if data is not None and not isinstance(data, bytes):
        raise TypeError(f"'data' must be bytes, got {type(data).__name__}")

    if url is not None:
        file_part = Part(url=url, media_type=mime_type)
    else:
        file_part = Part(raw=data, media_type=mime_type)

    return Artifact(
        artifact_id=artifact_id or str(uuid4()),
        name=name or "file",
        parts=[file_part],
    )


def data_artifact(
    data: dict,
    *,
    name: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Build an Artifact carrying a single structured-data part.

    Args:
        data: JSON-serializable dict to include as a Protobuf Value.
        name: Human-readable artifact name. Defaults to "data".
        artifact_id: Explicit artifact ID. Auto-generated if not provided.

    Returns:
        a2a.types.Artifact with a single DataPart.
    """
    proto_value = struct_pb2.Value()
    json_format.ParseDict(data, proto_value)
    return Artifact(
        artifact_id=artifact_id or str(uuid4()),
        name=name or "data",
        parts=[Part(data=proto_value)],
    )


__all__ = ["file_artifact", "data_artifact"]
