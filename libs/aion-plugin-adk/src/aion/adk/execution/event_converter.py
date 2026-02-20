"""Converts ADK events directly to A2A protocol events."""

import uuid
from typing import Any

from a2a.types import (
    Artifact,
    FilePart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId, ArtifactName

from aion.adk.transformers import A2ATransformer

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()


class ADKToA2AEventConverter:
    """Converts ADK events directly to A2A protocol events.

    Handles partial (streaming) and non-partial (complete) ADK events.

    Partial ADK events are emitted as STREAM_DELTA artifact updates for live
    display. Non-partial events close the stream and emit a durable
    TaskStatusUpdateEvent with state=working.
    """

    def __init__(self, task_id: str, context_id: str):
        self._task_id = task_id
        self._context_id = context_id
        self._streaming_started = False

    def convert(self, adk_event: Any) -> list[AgentEvent]:
        """Convert an ADK event to zero or more A2A events.

        Args:
            adk_event: ADK Event object with content, partial, author fields.

        Returns:
            List of A2A events (may be empty if the event has no content).
        """
        if adk_event is None:
            return []

        is_partial = getattr(adk_event, "partial", False)

        if is_partial:
            if not hasattr(adk_event, "content") or not adk_event.content:
                return []
            return self._convert_partial(adk_event)
        else:
            return self._convert_non_partial(adk_event)

    def _convert_partial(self, adk_event: Any) -> list[AgentEvent]:
        """Emit a STREAM_DELTA artifact update for a partial (streaming) ADK event.

        The first chunk opens the artifact (append=False); subsequent chunks
        use append=True. All partial events carry last_chunk=False because the
        stream is only closed when the final non-partial event arrives.
        """
        parts = A2ATransformer.transform_content(adk_event.content)
        if not parts:
            return []

        append = self._streaming_started
        if not self._streaming_started:
            self._streaming_started = True

        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name=ArtifactName.STREAM_DELTA.value,
                parts=parts,
                metadata={"status": "active", "status_reason": "chunk_streaming"},
            ),
            append=append,
            last_chunk=False,
        )]

    def _convert_non_partial(self, adk_event: Any) -> list[AgentEvent]:
        """Convert a complete (non-partial) ADK event to A2A events.

        If streaming was active, the STREAM_DELTA is closed first with an
        empty last_chunk=True event. Each file part is then emitted as a
        standalone TaskArtifactUpdateEvent with a unique artifact id. All
        remaining text parts are grouped into a single TaskStatusUpdateEvent
        (state=working) so the client receives the durable message while the
        task is still running.
        """
        results: list[AgentEvent] = []

        if self._streaming_started:
            results.append(TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=Artifact(
                    artifact_id=ArtifactId.STREAM_DELTA.value,
                    name=ArtifactName.STREAM_DELTA.value,
                    parts=[],
                    metadata={"status": "completed"},
                ),
                append=True,
                last_chunk=True,
            ))
            self._streaming_started = False

        if not hasattr(adk_event, "content") or not adk_event.content:
            return results

        content_parts = A2ATransformer.transform_content(adk_event.content)
        if not content_parts:
            return results

        for idx, part in enumerate(content_parts):
            if isinstance(part.root, FilePart):
                results.append(TaskArtifactUpdateEvent(
                    task_id=self._task_id,
                    context_id=self._context_id,
                    artifact=Artifact(
                        artifact_id=str(uuid.uuid4()),
                        name=ArtifactName.OUTPUT_FILE.value,
                        parts=[part],
                        metadata={"file_index": idx},
                    ),
                    append=False,
                    last_chunk=True,
                ))

        text_parts = [p for p in content_parts if not isinstance(p.root, FilePart)]
        if text_parts:
            author = getattr(adk_event, "author", "agent")
            role = Role.user if author == "user" else Role.agent
            msg = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=role,
                parts=text_parts,
            )
            results.append(TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                final=False,
                status=TaskStatus(state=TaskState.working, message=msg),
            ))

        return results

    def convert_pending_stream(self, delta_text: str) -> list[AgentEvent]:
        """Close any open STREAM_DELTA and emit accumulated text as working status.

        Called when the agent stream ends with active streaming — partial events
        arrived but no closing non-partial event followed. Handles the edge case
        where the last ADK event was partial, leaving an open stream artifact and
        unconfirmed text that needs to be emitted before the terminal event.
        """
        results: list[AgentEvent] = []

        if self._streaming_started:
            results.append(TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=Artifact(
                    artifact_id=ArtifactId.STREAM_DELTA.value,
                    name=ArtifactName.STREAM_DELTA.value,
                    parts=[],
                    metadata={"status": "completed"},
                ),
                append=True,
                last_chunk=True,
            ))
            self._streaming_started = False

        if delta_text:
            msg = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=delta_text))],
            )
            results.append(TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                final=False,
                status=TaskStatus(state=TaskState.working, message=msg),
            ))

        return results

    def convert_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the ADK agent finishes without error.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.completed),
        )

    def convert_error(self, error: str, error_type: str) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=failed and log the error.

        Called when the ADK agent raises an unhandled exception. The error
        details are logged at ERROR level but are not forwarded to the client.
        """
        logger.error(f"Execution error: {error}, type={error_type}")
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.failed),
        )

__all__ = ["ADKToA2AEventConverter"]
