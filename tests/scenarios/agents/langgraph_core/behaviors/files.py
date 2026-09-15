"""File parts in both directions: what arrived, and what the agent sends out."""

from __future__ import annotations

from uuid import uuid4

from a2a.types import Artifact, Part
from aion.core.a2a import file_artifact
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import (
    ARTIFACT_DATA_NAME,
    ARTIFACT_FILE_BYTES,
    ARTIFACT_FILE_MEDIA_TYPE,
    ARTIFACT_FILE_NAME,
    ARTIFACT_URL,
    ARTIFACT_URL_NAME,
    artifacts_data_parts,
    artifacts_text,
    parts_text,
)

__all__ = ["parts", "artifacts"]


async def parts(invocation: Invocation) -> None:
    """Report every part of the inbound message, as the agent received it."""
    inbox = invocation.context.inbox
    message = inbox.message if inbox is not None else None
    received = list(message.parts) if message is not None else []

    await invocation.thread.reply(
        parts_text(
            kinds=[part.WhichOneof("content") or "empty" for part in received],
            urls=[part.url for part in received if part.url],
            raw_bytes=sum(len(part.raw) for part in received if part.raw),
        )
    )


def _value(payload: dict) -> Value:
    """A data part's content: a protobuf Value wrapping this mapping."""
    return ParseDict(payload, Value())


async def artifacts(invocation: Invocation) -> None:
    """Emit a data artifact, an inline file and a url, then say so."""
    thread = invocation.thread

    await thread.post(
        Artifact(
            artifact_id=uuid4().hex,
            name=ARTIFACT_DATA_NAME,
            parts=[Part(data=_value(payload)) for payload in artifacts_data_parts()],
        )
    )
    await thread.post(
        file_artifact(
            ARTIFACT_FILE_BYTES,
            mime_type=ARTIFACT_FILE_MEDIA_TYPE,
            name=ARTIFACT_FILE_NAME,
        )
    )
    await thread.post(
        Artifact(
            artifact_id=uuid4().hex,
            name=ARTIFACT_URL_NAME,
            parts=[Part(url=ARTIFACT_URL, media_type=ARTIFACT_FILE_MEDIA_TYPE)],
        )
    )
    await thread.reply(artifacts_text())
