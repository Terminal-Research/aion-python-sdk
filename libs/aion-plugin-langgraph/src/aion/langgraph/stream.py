"""Streaming helpers for emitting events from LangGraph nodes.

This module provides helper functions to emit custom events during graph execution.
All events are emitted via LangGraph's custom stream mode.
"""

from typing import Any, Optional
from uuid import uuid4

from a2a.types import (
    Artifact,
    Part,
    FilePart,
    FileWithBytes,
    FileWithUri,
    DataPart,
)
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import StreamWriter

from .events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    TaskUpdateCustomEvent,
)


def emit_file_artifact(
    writer: StreamWriter,
    *,
    url: str | None = None,
    base64: str | None = None,
    mime_type: str,
    name: str | None = None,
    append: bool = False,
    is_last_chunk: bool = True,
) -> None:
    """Emit a file artifact during graph execution.

    Accepts file parameters compatible with LangChain FileContentBlock and
    converts them to an a2a Artifact with FilePart.

    Args:
        writer: LangGraph StreamWriter from node signature
        url: File URL (for FileWithUri)
        base64: File content as base64 string (for FileWithBytes)
        mime_type: MIME type of the file (e.g., "application/pdf", "image/png")
        name: Artifact name (defaults to "file")
        append: If True, append to previously sent artifact
        is_last_chunk: If True, this is the final chunk

    Raises:
        ValueError: If neither url nor base64 is provided, or if both are provided

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Emit file by URL
            emit_file(
                writer,
                url="https://example.com/report.pdf",
                mime_type="application/pdf",
                name="analysis_report"
            )

            # Emit file by base64
            emit_file(
                writer,
                base64="JVBERi0xLjQK...",
                mime_type="application/pdf",
                name="generated_document"
            )

            # Streaming file chunks
            for i, chunk_base64 in enumerate(file_chunks):
                is_last = (i == len(file_chunks) - 1)
                emit_file(
                    writer,
                    base64=chunk_base64,
                    mime_type="text/plain",
                    append=True,
                    is_last_chunk=is_last
                )

            return state
    """
    if not url and not base64:
        raise ValueError("Either 'url' or 'base64' must be provided")
    if url and base64:
        raise ValueError("Provide either 'url' or 'base64', not both")

    if url:
        file_data = FileWithUri(uri=url, mime_type=mime_type)
    else:
        file_data = FileWithBytes(bytes=base64, mime_type=mime_type)

    artifact = Artifact(
        artifact_id=str(uuid4()),
        name=name or "file",
        parts=[Part(root=FilePart(file=file_data))]
    )

    writer(ArtifactCustomEvent(
        artifact=artifact,
        append=append,
        is_last_chunk=is_last_chunk,
    ))


def emit_data_artifact(
    writer: StreamWriter,
    data: dict | Any,
    name: str | None = None,
    append: bool = False,
    is_last_chunk: bool = True,
) -> None:
    """Emit a data artifact during graph execution.

    Creates an artifact with structured data (JSON-serializable).

    Args:
        writer: LangGraph StreamWriter from node signature
        data: Data to emit (dict or any JSON-serializable value)
        name: Artifact name (defaults to "data")
        append: If True, append to previously sent artifact
        is_last_chunk: If True, this is the final chunk

    Example:
        def my_node(state: dict, writer: StreamWriter):
            emit_data(writer, {"status": "success", "results": [...]}, name="analysis_results")
    """
    artifact = Artifact(
        artifact_id=str(uuid4()),
        name=name or "data",
        parts=[Part(root=DataPart(data=data))]
    )

    writer(ArtifactCustomEvent(
        artifact=artifact,
        append=append,
        is_last_chunk=is_last_chunk,
    ))


def emit_message(
    writer: StreamWriter,
    message: AIMessage | AIMessageChunk,
    ephemeral: bool = False,
) -> None:
    """Emit a message event during graph execution.

    Supports both full messages and streaming chunks:
    - AIMessage → TaskStatusUpdateEvent(working, message=...)
    - AIMessageChunk → TaskArtifactUpdateEvent(STREAM_DELTA) for real-time streaming

    When ephemeral=True, both AIMessage and AIMessageChunk produce a
    TaskArtifactUpdateEvent(EPHEMERAL_MESSAGE) that is sent to the client
    but filtered out by the task store (not persisted in task history).

    Args:
        writer: LangGraph StreamWriter from node signature
        message: LangChain message to emit (AIMessage or AIMessageChunk)
        ephemeral: If True, message is not persisted in task history

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Ephemeral progress notification (not saved)
            emit_message(writer, AIMessage(content="Searching..."), ephemeral=True)

            # Full message (saved in history)
            emit_message(writer, AIMessage(content="Done"))
    """
    writer(MessageCustomEvent(message=message, ephemeral=ephemeral))


def emit_task_update(
    writer: StreamWriter,
    message: Optional[AIMessage] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Emit a combined task update with message and/or metadata in a single event.

    Produces exactly one TaskStatusUpdateEvent(working, message=..., metadata=...).
    Use this when you need to emit both a message and metadata simultaneously.
    For streaming chunks, use emit_message() with AIMessageChunk instead.

    Args:
        writer: LangGraph StreamWriter from node signature
        message: Full message to emit (AIMessage only, not chunks)
        metadata: Metadata dict to merge into the task

    Raises:
        ValueError: If neither message nor metadata is provided

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Message + metadata together as one event
            emit_task_update(
                writer,
                message=AIMessage(content="Analysis complete"),
                metadata={"progress": 100, "step": "done"},
            )

            # Metadata only
            emit_task_update(writer, metadata={"progress": 50})

            # Message only (equivalent to emit_message with AIMessage)
            emit_task_update(writer, message=AIMessage(content="Done"))
    """
    if message is None and metadata is None:
        raise ValueError("At least one of 'message' or 'metadata' must be provided")

    writer(TaskUpdateCustomEvent(message=message, metadata=metadata))
