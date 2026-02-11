"""Converts LangGraph events directly to A2A protocol events."""

import uuid
from typing import Any, Optional, Tuple

from a2a.types import (
    Artifact,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from aion.shared.agent.adapters import InterruptInfo
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId, ArtifactName
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from ..events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    TaskUpdateCustomEvent,
)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()

SKIP_EVENTS = ("values", "updates")


class LangGraphA2AConverter:
    """Converts LangGraph events directly to A2A protocol events.

    Single converter that handles all LangGraph event types and produces
    A2A events without an intermediate ExecutionEvent representation.
    """

    def __init__(self, task_id: str, context_id: str):
        self._task_id = task_id
        self._context_id = context_id
        self._streaming_started = False

    def convert(
            self,
            event_type: str,
            event_data: Any,
            metadata: Optional[Any] = None,
    ) -> list[AgentEvent]:
        """Convert a LangGraph event to zero or more A2A events.

        Args:
            event_type: LangGraph event type (messages, custom, values, updates).
            event_data: LangGraph event data.
            metadata: Optional event metadata (only present for "messages" events).

        Returns:
            List of A2A events (may be empty for skipped event types).
        """
        if event_type == "messages":
            return self._convert_message(event_data, metadata)
        elif event_type == "custom":
            return self._convert_custom(event_data)
        elif event_type in SKIP_EVENTS:
            return []
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return []

    def _convert_message(self, message: Any, metadata: Optional[Any]) -> list[AgentEvent]:
        """Route a LangGraph message event to the appropriate conversion path.

        Detects whether the message is an AIMessageChunk (streaming) or a
        complete message, then delegates accordingly.
        """
        is_chunk, is_last_chunk = self._detect_chunk(message)
        if is_chunk:
            return self._convert_streaming_chunk(message, is_last_chunk)
        return self._convert_full_message(message)

    def _convert_streaming_chunk(self, message: Any, is_last_chunk: bool) -> list[AgentEvent]:
        """Emit a stream-delta artifact event for a single AIMessageChunk.

        Empty intermediate chunks are skipped. The first chunk sets append=False
        to open the artifact; subsequent chunks use append=True. The last chunk
        carries last_chunk=True so the client can close the stream.
        """
        text = self._get_text_content(message)
        if not text and not is_last_chunk:
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
            last_chunk=is_last_chunk,
        )]

    def _convert_full_message(self, message: Any) -> list[AgentEvent]:
        """Convert a complete (non-streaming) LangGraph message to A2A events.

        Each file part in the message content is emitted as a standalone
        TaskArtifactUpdateEvent with a unique artifact id. All remaining text
        parts are grouped into a single TaskStatusUpdateEvent (state=working)
        so the client sees the message while the task is still running.
        """
        content = self._extract_content_parts(message)
        results: list[AgentEvent] = []

        for idx, part in enumerate(content):
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

        text_parts = [p for p in content if not isinstance(p.root, FilePart)]
        if text_parts:
            role_str = self._detect_role(message)
            role = Role(role_str)
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

    def _convert_custom(self, event_data: Any) -> list[AgentEvent]:
        """Dispatch a typed custom event to the appropriate handler.

        Supports ArtifactCustomEvent (raw artifact passthrough),
        MessageCustomEvent (ephemeral or regular message), and
        TaskUpdateCustomEvent (status/metadata update). Unknown types are
        logged and silently dropped.
        """
        if isinstance(event_data, ArtifactCustomEvent):
            return [TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=event_data.artifact,
                append=event_data.append,
                last_chunk=event_data.is_last_chunk,
            )]

        if isinstance(event_data, MessageCustomEvent):
            if event_data.ephemeral:
                return self._convert_ephemeral(event_data.message)
            return self._convert_message(event_data.message, metadata=None)

        if isinstance(event_data, TaskUpdateCustomEvent):
            return self._convert_task_update(event_data)

        logger.warning(f"Ignoring unknown custom event type: {type(event_data)}")
        return []

    def _convert_task_update(self, event: TaskUpdateCustomEvent) -> list[AgentEvent]:
        """Convert a TaskUpdateCustomEvent to a working-status update.

        Builds an optional text message from event.message and strips internal
        aion:-prefixed keys from event.metadata before forwarding. Returns an
        empty list when neither a message nor public metadata is present.
        """
        msg: Optional[Message] = None
        if event.message is not None:
            text = self._get_text_content(event.message)
            if text:
                msg = Message(
                    context_id=self._context_id,
                    task_id=self._task_id,
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=text))],
                )

        filtered: Optional[dict] = None
        if event.metadata:
            filtered = {k: v for k, v in event.metadata.items() if not k.startswith("aion:")} or None

        if not msg and not filtered:
            return []

        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=False,
            metadata=filtered,
            status=TaskStatus(state=TaskState.working, message=msg),
        )]

    def _convert_ephemeral(self, message: Any) -> list[AgentEvent]:
        """Emit a transient message artifact that is displayed but not persisted.

        Uses the fixed EPHEMERAL_MESSAGE artifact id so the client knows to
        treat the content as a status hint rather than durable output.
        Returns an empty list when the message has no text content.
        """
        text = self._get_text_content(message)
        if not text:
            return []
        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.EPHEMERAL_MESSAGE.value,
                name=ArtifactName.EPHEMERAL_MESSAGE.value,
                parts=[Part(root=TextPart(text=text))],
            ),
            append=False,
            last_chunk=True,
        )]

    def convert_interrupt(self, interrupts: list[InterruptInfo]) -> TaskStatusUpdateEvent:
        """Produce an input_required status event from a list of interrupt infos.

        Uses the first interrupt to build the prompt message and attaches its id
        as message metadata so the client can resume the correct thread.
        """
        message: Optional[Message] = None
        interrupt_id: Optional[str] = None

        if interrupts:
            info = interrupts[0]
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
            f"interrupts_count={len(interrupts)}, "
            f"interrupt_id={interrupt_id or 'N/A'}"
        )

        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=False,
            status=TaskStatus(state=TaskState.input_required, message=message),
        )

    def convert_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the LangGraph graph finishes without error or interrupt.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.completed),
        )

    def convert_error(self, error: str, error_type: str) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=failed and log the error.

        Called when the LangGraph graph raises an unhandled exception. The error
        details are logged at ERROR level but are not forwarded to the client.
        """
        logger.error(f"Execution error: {error}, type={error_type}")
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            final=True,
            status=TaskStatus(state=TaskState.failed),
        )

    @staticmethod
    def _detect_chunk(message: Any) -> Tuple[bool, bool]:
        """Detect whether a message is a streaming chunk and whether it is the last one.

        Checks for AIMessageChunk type and reads the chunk_position attribute
        set by the event preprocessor.

        Returns:
            Tuple of (is_chunk, is_last_chunk).
        """
        is_chunk = isinstance(message, AIMessageChunk)
        is_last_chunk = False
        if is_chunk and hasattr(message, "chunk_position"):
            is_last_chunk = message.chunk_position == "last"
        return is_chunk, is_last_chunk

    @staticmethod
    def _detect_role(message: Any) -> str:
        """Map a LangChain message type to an A2A role string.

        AIMessage, AIMessageChunk, and SystemMessage map to "agent";
        HumanMessage maps to "user". Defaults to "agent" for unknown types.
        """
        if isinstance(message, (AIMessage, AIMessageChunk, SystemMessage)):
            return "agent"
        if isinstance(message, HumanMessage):
            return "user"
        return "agent"

    @staticmethod
    def _get_text_content(message: Any) -> str:
        """Extract a plain text string from a LangChain message.

        Handles string content, list-of-dicts content blocks (OpenAI style),
        and arbitrary objects by falling back to str().
        """
        if not hasattr(message, "content"):
            return str(message)
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return str(content)

    @staticmethod
    def _extract_content_parts(message: Any) -> list[Part]:
        """Extract content parts from a LangGraph message as A2A Part objects.

        Handles both plain string content and multi-part list content
        (OpenAI-style blocks). Each dict item is converted via
        _convert_dict_part; non-dict items are wrapped in TextPart.
        """
        if not hasattr(message, "content"):
            return [Part(root=TextPart(text=str(message)))]

        raw_content = message.content

        if not isinstance(raw_content, list):
            return [Part(root=TextPart(text=str(raw_content)))]

        parts: list[Part] = []
        for item in raw_content:
            if isinstance(item, dict):
                parts.append(LangGraphA2AConverter._convert_dict_part(item))
            else:
                parts.append(Part(root=TextPart(text=str(item))))

        return parts

    @staticmethod
    def _convert_dict_part(part: dict) -> Part:
        """Convert a raw content-part dict to an A2A Part.

        Supports type="text" (produces TextPart) and type="file" (produces
        FilePart with either a URI or base64-encoded bytes). Unknown types are
        logged and serialised as plain text.
        """
        part_type = part.get("type", "text")

        if part_type == "text":
            return Part(root=TextPart(text=part.get("text", "")))

        if part_type == "file":
            mime_type = part.get("mime_type", "application/octet-stream")
            url = part.get("url")
            if url:
                file_data = FileWithUri(uri=url, mime_type=mime_type)
            else:
                file_data = FileWithBytes(bytes=part.get("base64", ""), mime_type=mime_type)
            return Part(root=FilePart(file=file_data))

        logger.warning(f"Unknown content part type: {part_type}")
        return Part(root=TextPart(text=str(part)))
