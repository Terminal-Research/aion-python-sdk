"""Converts LangGraph events directly to A2A protocol events."""

import uuid
from typing import Any, Optional

from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from aion.shared.agent.adapters import InterruptInfo
from aion.shared.constants import (
    MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_ACTION_PAYLOAD_SCHEMA_V1,
)
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId, ArtifactName
from aion.shared.types.a2a.extensions.messaging import MessageActionPayload
from google.protobuf import json_format, struct_pb2
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from ..converters.lc_to_a2a import LcToA2AConverter
from ..events.custom_events import (
    ArtifactCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    TaskUpdateCustomEvent,
)

LangChainAgentMessage = AIMessage | AIMessageChunk
SupportedCustomEvents = ArtifactCustomEvent | MessageCustomEvent | ReactionCustomEvent | TaskUpdateCustomEvent

A2AAgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

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

    def convert(self, event_type: str, event_data: Any) -> list[A2AAgentEvent]:
        """Convert a LangGraph event to zero or more A2A events.

        Args:
            event_type: LangGraph event type (messages, custom, values, updates).
            event_data: LangGraph event data.

        Returns:
            List of A2A events (may be empty for skipped event types).
        """
        if event_type == "messages":
            return self._convert_message(event_data)
        elif event_type == "custom":
            return self._convert_custom(event_data)
        elif event_type in SKIP_EVENTS:
            return []
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return []

    def _convert_message(self, message: AIMessage | AIMessageChunk) -> list[A2AAgentEvent]:
        """Convert an AIMessage or AIMessageChunk to A2A events."""
        if isinstance(message, AIMessageChunk):
            return self._convert_streaming_chunk(message)
        return self._convert_full_message(message)

    def _convert_streaming_chunk(self, message: AIMessageChunk) -> list[A2AAgentEvent]:
        """Emit a stream-delta artifact event for a single AIMessageChunk.

        Empty intermediate chunks are skipped. The first chunk sets append=False
        to open the artifact; subsequent chunks use append=True. The last chunk
        carries last_chunk=True so the client can close the stream.
        """
        is_last_chunk = message.chunk_position == "last"
        parts = LcToA2AConverter.from_message(message)
        if not parts and not is_last_chunk:
            return []

        append = self._streaming_started
        self._streaming_started = True

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

    def _convert_full_message(self, message: AIMessage) -> list[A2AAgentEvent]:
        """Convert a complete (non-streaming) LangGraph message to A2A events.

        All content parts (text, file, etc.) are grouped into a single
        TaskStatusUpdateEvent (state=working). Artifacts must be emitted
        explicitly via ArtifactCustomEvent — no automatic promotion of
        FilePart to TaskArtifactUpdateEvent.
        """
        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []

        role = self._detect_role(message)
        # Use message.id if set (e.g. from reply), otherwise generate a new one
        message_id = message.id or str(uuid.uuid4())
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id,
            role=role,
            parts=a2a_parts,
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_custom(self, event_data: SupportedCustomEvents) -> list[A2AAgentEvent]:
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
            if event_data.routing is not None:
                return self._convert_message_with_routing(event_data.message, event_data.routing)
            return self._convert_message(event_data.message)

        if isinstance(event_data, ReactionCustomEvent):
            return self._convert_reaction(event_data)

        if isinstance(event_data, TaskUpdateCustomEvent):
            return self._convert_task_update(event_data)

        logger.warning(f"Ignoring unknown custom event type: {type(event_data)}")
        return []

    @staticmethod
    def _build_extension_part(data: dict, schema_uri: str) -> Part:
        proto_value = struct_pb2.Value()
        json_format.ParseDict(data, proto_value)
        return Part(
            data=proto_value,
            metadata={MESSAGING_EXTENSION_URI_V1: {"schema": schema_uri}},
        )

    def _convert_message_with_routing(
            self,
            message: AIMessage,
            routing: MessageActionPayload,
    ) -> list[A2AAgentEvent]:
        """Convert a message with explicit routing target to an A2A event.

        Produces a TaskStatusUpdateEvent whose message contains both the text
        parts from the AIMessage and a DataPart carrying the MessageActionPayload
        so the distribution knows where to deliver the message.
        """
        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []

        a2a_parts.append(self._build_extension_part(
            routing.model_dump(by_alias=True, exclude_none=True),
            MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
        ))

        message_id = message.id or str(uuid.uuid4())
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id,
            role=Role.ROLE_AGENT,
            parts=a2a_parts,
            extensions=[MESSAGING_EXTENSION_URI_V1],
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_reaction(self, event: ReactionCustomEvent) -> list[A2AAgentEvent]:
        """Convert a ReactionCustomEvent to an A2A event.

        Produces a TaskStatusUpdateEvent whose message contains a single
        DataPart carrying the ReactionActionPayload so the distribution
        can add or remove the reaction on the provider message.
        """
        data_part = self._build_extension_part(
            event.payload.model_dump(by_alias=True, exclude_none=True),
            REACTION_ACTION_PAYLOAD_SCHEMA_V1,
        )
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=[data_part],
            extensions=[MESSAGING_EXTENSION_URI_V1],
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_task_update(self, event: TaskUpdateCustomEvent) -> list[A2AAgentEvent]:
        """Convert a TaskUpdateCustomEvent to a working-status update.

        Builds an optional text message from event.message and strips internal
        aion:-prefixed keys from event.metadata before forwarding. Returns an
        empty list when neither a message nor public metadata is present.
        """
        msg: Optional[Message] = None
        if event.message is not None:
            parts = LcToA2AConverter.from_message(event.message)
            if parts:
                message_id = event.message.id or str(uuid.uuid4())
                msg = Message(
                    context_id=self._context_id,
                    task_id=self._task_id,
                    message_id=message_id,
                    role=Role.ROLE_AGENT,
                    parts=parts,
                )

        filtered: Optional[dict] = None
        if event.metadata:
            filtered = {k: v for k, v in event.metadata.items() if not k.startswith("aion:")} or None

        if not msg and not filtered:
            return []

        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            metadata=filtered,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_ephemeral(self, message: LangChainAgentMessage) -> list[A2AAgentEvent]:
        """Emit a transient message artifact that is displayed but not persisted.

        Uses the fixed EPHEMERAL_MESSAGE artifact id so the client knows to
        treat the content as a status hint rather than durable output.
        Returns an empty list when the message has no text content.
        """
        parts = LcToA2AConverter.from_message(message)
        if not parts:
            return []

        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.EPHEMERAL_MESSAGE.value,
                name=ArtifactName.EPHEMERAL_MESSAGE.value,
                parts=parts,
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
                role=Role.ROLE_AGENT,
                parts=[Part(text=info.get_prompt_text())],
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
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED, message=message),
        )

    def convert_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the LangGraph graph finishes without error or interrupt.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
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
            status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
        )

    @staticmethod
    def _detect_role(message: LangChainAgentMessage) -> Role:
        """Map a LangChain message type to an A2A Role.

        AIMessage and AIMessageChunk map to ROLE_AGENT;
        HumanMessage maps to ROLE_USER. Defaults to ROLE_AGENT for unknown types.
        """
        if isinstance(message, HumanMessage):
            return Role.ROLE_USER
        return Role.ROLE_AGENT
