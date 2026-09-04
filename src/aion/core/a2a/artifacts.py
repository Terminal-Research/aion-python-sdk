"""Framework-agnostic artifact builders.

Constructs a2a.types.Artifact objects without any framework-specific details.
Used directly in Thread.post() and in emit_artifact() helpers across frameworks.
"""

from __future__ import annotations

from uuid import uuid4

from a2a.types import Artifact, Part
from google.protobuf import json_format, struct_pb2


def url_artifact(
    url: str,
    *,
    mime_type: str,
    name: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Build an Artifact referencing a remote file by URL.

    Args:
        url: Remote file URL (e.g. "https://cdn.example.com/report.pdf").
        mime_type: MIME type of the file (e.g. "application/pdf", "image/png").
        name: Human-readable artifact name. Defaults to "file".
        artifact_id: Explicit artifact ID. Auto-generated if not provided.

    Returns:
        a2a.types.Artifact with a single FilePart (FileWithUri).
    """
    return Artifact(
        artifact_id=artifact_id or str(uuid4()),
        name=name or "file",
        parts=[Part(url=url, media_type=mime_type)],
    )


def file_artifact(
    data: bytes,
    *,
    mime_type: str,
    name: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Build an Artifact carrying inline file content as bytes.

    Args:
        data: File content as bytes.
        mime_type: MIME type of the file (e.g. "text/plain", "image/png").
        name: Human-readable artifact name. Defaults to "file".
        artifact_id: Explicit artifact ID. Auto-generated if not provided.

    Returns:
        a2a.types.Artifact with a single FilePart (FileWithBytes).

    Raises:
        TypeError: If data is not bytes.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"'data' must be bytes, got {type(data).__name__}")
    return Artifact(
        artifact_id=artifact_id or str(uuid4()),
        name=name or "file",
        parts=[Part(raw=data, media_type=mime_type)],
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


__all__ = ["url_artifact", "file_artifact", "data_artifact"]
