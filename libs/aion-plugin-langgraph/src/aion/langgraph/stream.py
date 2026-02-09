"""Streaming helpers for emitting events from LangGraph nodes.

This module provides helper functions to emit custom events during graph execution.
All events are emitted via LangGraph's custom stream mode and converted to
ExecutionEvent types by the plugin's event converter.
"""

from typing import Any
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
    TaskMetadataCustomEvent,
)


def emit_file(
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
    # Validate parameters
    if not url and not base64:
        raise ValueError("Either 'url' or 'base64' must be provided")
    if url and base64:
        raise ValueError("Provide either 'url' or 'base64', not both")

    # Create FilePart based on provided parameters
    if url:
        file_data = FileWithUri(uri=url, mime_type=mime_type)
    else:
        file_data = FileWithBytes(bytes=base64, mime_type=mime_type)

    # Create Artifact with FilePart
    artifact = Artifact(
        artifact_id=str(uuid4()),
        name=name or "file",
        parts=[Part(root=FilePart(file=file_data))]
    )

    # Emit via custom event
    event = ArtifactCustomEvent(
        artifact=artifact,
        append=append,
        is_last_chunk=is_last_chunk,
    )
    writer(event)


def emit_data(
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
            # Emit analysis results
            emit_data(writer, {
                "status": "success",
                "results": [...],
                "metrics": {"accuracy": 0.95}
            }, name="analysis_results")

            # Streaming data chunks
            for i, data_chunk in enumerate(data_chunks):
                is_last = (i == len(data_chunks) - 1)
                emit_data(
                    writer,
                    data_chunk,
                    name="streaming_data",
                    append=True,
                    is_last_chunk=is_last
                )

            return state
    """
    # Create Artifact with DataPart
    artifact = Artifact(
        artifact_id=str(uuid4()),
        name=name or "data",
        parts=[Part(root=DataPart(data=data))]
    )

    # Emit via custom event
    event = ArtifactCustomEvent(
        artifact=artifact,
        append=append,
        is_last_chunk=is_last_chunk,
    )
    writer(event)


def emit_message(
    writer: StreamWriter,
    message: AIMessage | AIMessageChunk,
) -> None:
    """Emit a message event during graph execution.

    Accepts LangChain AIMessage or AIMessageChunk for programmatic message emission.

    Args:
        writer: LangGraph StreamWriter from node signature
        message: LangChain message to emit (AIMessage or AIMessageChunk)

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Emit a programmatic message
            emit_message(writer, AIMessage(content="Step complete"))

            # Emit streaming chunk
            emit_message(writer, AIMessageChunk(content="chunk..."))

            return state
    """
    # Emit via custom event
    event = MessageCustomEvent(message=message)
    writer(event)


def emit_task_metadata(
    writer: StreamWriter,
    metadata: dict[str, Any],
) -> None:
    """Emit task metadata update during graph execution.

    Metadata is merged with existing task metadata on the server side.

    Args:
        writer: LangGraph StreamWriter from node signature
        metadata: Metadata dictionary to merge into task

    Example:
        def my_node(state: dict, writer: StreamWriter):
            # Update progress
            emit_task_metadata(writer, {
                "progress": 50,
                "step": "analysis",
                "custom_field": "value"
            })

            return state
    """
    # Emit via custom event
    event = TaskMetadataCustomEvent(metadata=metadata)
    writer(event)
