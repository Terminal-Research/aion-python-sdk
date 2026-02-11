"""Converts internal LangGraph ExecutionEvents to A2A protocol events."""

import uuid
from typing import Optional

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
from aion.shared.agent.adapters import (
    ArtifactEvent,
    CompleteEvent,
    ErrorEvent,
    ExecutionEvent,
    InterruptEvent,
    MessageEvent,
    NodeUpdateEvent,
    StateUpdateEvent,
)
from aion.shared.agent.execution import set_langgraph_node
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId, ArtifactName

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()


class LangGraphA2AConverter:
    """Converts LangGraph ExecutionEvents to A2A protocol events.

    Encapsulates all translation logic that was previously in the server-side
    ExecutionEventHandler, keeping it within the plugin boundary.
    """

    def __init__(self, task_id: str, context_id: str):
        self._task_id = task_id
        self._context_id = context_id
        self._streaming_started = False

    def convert(self, event: ExecutionEvent) -> list[AgentEvent]:
        """Convert a single ExecutionEvent to zero or more A2A events."""
        if isinstance(event, MessageEvent):
            return self._convert_message(event)
        elif isinstance(event, ArtifactEvent):
            return [self._convert_artifact(event)]
        elif isinstance(event, InterruptEvent):
            return [self._convert_interrupt(event)]
        elif isinstance(event, CompleteEvent):
            return [self._convert_complete()]
        elif isinstance(event, ErrorEvent):
            return [self._convert_error(event)]
        elif isinstance(event, NodeUpdateEvent):
            self._handle_node_update(event)
            return []
        elif isinstance(event, StateUpdateEvent):
            return self._convert_state_update(event)
        else:
            logger.warning(f"Unknown execution event type: {event.event_type}")
            return []

    def _convert_message(self, event: MessageEvent) -> list[AgentEvent]:
        if event.is_chunk:
            return self._convert_streaming_chunk(event)
        return self._convert_full_message(event)

    def _convert_streaming_chunk(self, event: MessageEvent) -> list[AgentEvent]:
        text = event.get_text_content()
        if not text and not event.is_last_chunk:
            return []

        append = self._streaming_started
        if not self._streaming_started:
            self._streaming_started = True

        parts = [Part(root=TextPart(text=text))] if text else []
        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name=ArtifactName.STREAM_DELTA.value,
                parts=parts,
                metadata={
                    "status": "active",
                    "status_reason": "chunk_streaming",
                },
            ),
            append=append,
            last_chunk=event.is_last_chunk,
        )]

    def _convert_full_message(self, event: MessageEvent) -> list[AgentEvent]:
        results: list[AgentEvent] = []

        # File parts become separate artifacts
        for idx, part in enumerate(event.content):
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

        # Text parts become a status message
        text_parts = [p for p in event.content if not isinstance(p.root, FilePart)]
        if text_parts:
            role = Role(event.role) if event.role else Role.agent
            message = Message(
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
                status=TaskStatus(state=TaskState.working, message=message),
            ))

        return results

    def _convert_artifact(self, event: ArtifactEvent) -> TaskArtifactUpdateEvent:
        return TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=event.artifact,
            append=event.append,
            last_chunk=event.is_last_chunk,
        )

    def _convert_interrupt(self, event: InterruptEvent) -> TaskStatusUpdateEvent:
        message: Optional[Message] = None
        interrupt_id: Optional[str] = None

        if event.interrupts:
            info = event.interrupts[0]
            interrupt_id = info.id
            message = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=Role.agent,
                parts=[Part(root=TextPart(text=info.get_prompt_text()))],
                metadata={"interruptId": interrupt_id},
            )

        logger.info(
            f"Interrupted (requires input), "
            f"interrupts_count={len(event.interrupts)}, "
            f"interrupt_id={interrupt_id or 'N/A'}"
        )

        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=False,
            status=TaskStatus(state=TaskState.input_required, message=message),
        )

    def _convert_complete(self) -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.completed),
        )

    def _convert_error(self, event: ErrorEvent) -> TaskStatusUpdateEvent:
        logger.error(f"Execution error: {event.error}, type={event.error_type}")
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.failed),
        )

    @staticmethod
    def _handle_node_update(event: NodeUpdateEvent) -> None:
        if event.node_name:
            set_langgraph_node(event.node_name)
            logger.debug(f"Node: {event.node_name}")

    def _convert_state_update(self, event: StateUpdateEvent) -> list[AgentEvent]:
        task_metadata = event.data.get("task_metadata")
        if not task_metadata:
            return []

        filtered = {k: v for k, v in task_metadata.items() if not k.startswith("aion:")}
        if not filtered:
            return []

        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=False,
            metadata=filtered,
            status=TaskStatus(state=TaskState.working),
        )]
