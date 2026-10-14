"""File parts in both directions: what arrived, and what the agent sends out."""

from __future__ import annotations

import base64
from typing import Optional
from uuid import uuid4

from a2a.types import Artifact, Message, Part, Role, Task
from aion.core.a2a import A2AOutbox, FileActionPayload, file_artifact
from langchain_core.messages import AIMessage
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import (
    ARTIFACT_DATA_NAME,
    ARTIFACT_FILE_BYTES,
    ARTIFACT_FILE_MEDIA_TYPE,
    ARTIFACT_FILE_NAME,
    ARTIFACT_URL,
    ARTIFACT_URL_NAME,
    SEND_FILE_BYTES,
    SEND_FILE_MEDIA_TYPE,
    SEND_FILE_NAME,
    SEND_FILE_TEXT,
    artifacts_data_parts,
    artifacts_text,
    parts_text,
)

__all__ = ["parts", "artifacts", "send_file"]


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
            data=[MessageToDict(part.data) for part in received if part.HasField("data")],
            data_metadata=[
                MessageToDict(part.metadata) for part in received if part.HasField("data")
            ],
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
            file_action=FileActionPayload(retention_expires_at=None),
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


def _file_part() -> Part:
    return Part(raw=SEND_FILE_BYTES, media_type=SEND_FILE_MEDIA_TYPE, filename=SEND_FILE_NAME)


async def send_file(invocation: Invocation) -> Optional[dict]:
    """Send one inline file by the route the argument names.

    ``reply`` is a model's answer with the file in it - an ``AIMessage``
    whose content blocks hold text and the file - so the file takes the road
    a model's attachment takes. The outbox routes return the payload as the
    graph's output.
    """
    route = invocation.command.argument.strip()
    if route == "artifact":
        await invocation.thread.post(
            file_artifact(SEND_FILE_BYTES, mime_type=SEND_FILE_MEDIA_TYPE, name=SEND_FILE_NAME)
        )
        return None
    if route == "reply":
        await invocation.thread.post(AIMessage(content=[
            {"type": "text", "text": SEND_FILE_TEXT},
            {"type": "file", "base64": base64.b64encode(SEND_FILE_BYTES).decode(),
             "mime_type": SEND_FILE_MEDIA_TYPE, "filename": SEND_FILE_NAME},
        ]))
        return None
    message = Message(
        message_id=uuid4().hex,
        role=Role.ROLE_AGENT,
        parts=[Part(text=SEND_FILE_TEXT), _file_part()],
    )
    if route == "outbox-message":
        return {"a2a_outbox": A2AOutbox(message=message)}
    if route == "outbox-task":
        return {"a2a_outbox": A2AOutbox(task=Task(
            history=[message],
            artifacts=[Artifact(artifact_id=uuid4().hex, name=SEND_FILE_NAME, parts=[_file_part()])],
        ))}
    await invocation.thread.reply(f"unknown route {route!r}")
    return None
