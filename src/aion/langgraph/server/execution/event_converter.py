"""Converts LangGraph events directly to A2A protocol events."""

import logging
import uuid
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
from aion.core.a2a import ArtifactId, ArtifactName
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from aion.core.constants import (
    CARDS_EXTENSION_URI_V1,
    MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_ACTION_PAYLOAD_SCHEMA_V1,
    STREAM_DELTA_PAYLOAD_SCHEMA_V1,
)
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
)
from aion.server.agent.adapters import InterruptInfo
from google.protobuf import json_format, struct_pb2
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from typing import Any, Optional

from ..converters.lc_to_a2a import LcToA2AConverter

LangChainAgentMessage = AIMessage | AIMessageChunk
SupportedCustomEvents = ArtifactCustomEvent | CardCustomEvent | MessageCustomEvent | ReactionCustomEvent

A2AAgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = logging.getLogger(__name__)

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

    def _convert_message(self, message: AIMessage | AIMessageChunk, metadata: dict | None = None) -> list[
        A2AAgentEvent]:
        """Convert an AIMessage or AIMessageChunk to A2A events."""
        if isinstance(message, AIMessageChunk):
            return self._convert_streaming_chunk(message, metadata=metadata)
        return self._convert_full_message(message, metadata=metadata)

    def _convert_streaming_chunk(self, message: AIMessageChunk, metadata: dict | None = None) -> list[A2AAgentEvent]:
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

        artifact_metadata: dict = {
            "status": "active",
            "status_reason": "chunk_streaming",
            MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1},
        }
        user_meta = {k: v for k, v in (metadata or {}).items() if not k.startswith("aion:")} or None
        if user_meta:
            artifact_metadata.update(user_meta)

        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name=ArtifactName.STREAM_DELTA.value,
                parts=parts,
                metadata=artifact_metadata,
            ),
            append=append,
            last_chunk=is_last_chunk,
        )]

    def _convert_full_message(self, message: AIMessage, metadata: dict | None = None) -> list[A2AAgentEvent]:
        """Convert a complete (non-streaming) LangGraph message to A2A events.

        All content parts (text, file, etc.) are grouped into a single
        TaskStatusUpdateEvent (state=working). Artifacts must be emitted
        explicitly via ArtifactCustomEvent — no automatic promotion of
        FilePart to TaskArtifactUpdateEvent.

        To send a card, pass a Card instance to Thread.post() instead of a
        plain JSX string — plain strings are always treated as regular text.
        """
        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []

        role = self._detect_role(message)
        message_id = message.id or str(uuid.uuid4())
        user_meta = {k: v for k, v in (metadata or {}).items() if not k.startswith("aion:")} or None
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id,
            role=role,
            parts=a2a_parts,
            metadata=user_meta,
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_card(
            self,
            card: Card,
            message_id: str | None = None,
            routing: MessageActionPayload | None = None,
            metadata: dict | None = None,
    ) -> list[A2AAgentEvent]:
        """Emit a card as a TaskStatusUpdateEvent with the CARDS_EXTENSION_URI_V1 extension."""
        parts = [build_card_a2a_part(card)]
        extensions = [CARDS_EXTENSION_URI_V1]
        if routing is not None:
            parts.append(self._build_extension_part(
                routing.model_dump(by_alias=True, exclude_none=True),
                MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
            ))
            extensions.append(MESSAGING_EXTENSION_URI_V1)
        user_meta = {k: v for k, v in (metadata or {}).items() if not k.startswith("aion:")} or None
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id or str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=parts,
            extensions=extensions,
            metadata=user_meta,
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_custom(self, event_data: SupportedCustomEvents) -> list[A2AAgentEvent]:
        """Dispatch a typed custom event to the appropriate handler.

        Supports ArtifactCustomEvent (raw artifact passthrough),
        CardCustomEvent (card message), MessageCustomEvent (ephemeral or
        regular message), and ReactionCustomEvent (reaction action). Unknown
        types are logged and silently dropped.
        """
        if isinstance(event_data, ArtifactCustomEvent):
            event_metadata: dict = {}
            if event_data.routing is not None:
                event_metadata[MESSAGING_EXTENSION_URI_V1] = event_data.routing.model_dump(by_alias=True, exclude_none=True)
            if event_data.metadata:
                user_meta = {k: v for k, v in event_data.metadata.items() if not k.startswith("aion:")}
                event_metadata.update(user_meta)
            return [TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=event_data.artifact,
                append=event_data.append,
                last_chunk=event_data.is_last_chunk,
                metadata=event_metadata or None,
            )]

        if isinstance(event_data, CardCustomEvent):
            return self._convert_card(event_data.card, routing=event_data.routing, metadata=event_data.metadata)

        if isinstance(event_data, MessageCustomEvent):
            if event_data.ephemeral:
                return self._convert_ephemeral(event_data.message)
            if event_data.routing is not None:
                return self._convert_message_with_routing(event_data.message, event_data.routing,
                                                          metadata=event_data.metadata)
            return self._convert_message(event_data.message, metadata=event_data.metadata)

        if isinstance(event_data, ReactionCustomEvent):
            return self._convert_reaction(event_data)

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
            metadata: dict | None = None,
    ) -> list[A2AAgentEvent]:
        """Convert a message with explicit routing target to an A2A event.

        Produces a TaskStatusUpdateEvent whose message contains both the content
        parts from the AIMessage and a DataPart carrying the MessageActionPayload
        so the distribution knows where to deliver the message.

        To send a routed card, pass a Card instance to Thread.reply() instead
        of a plain JSX string — plain strings are always treated as regular text.
        """
        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []

        a2a_parts.append(self._build_extension_part(
            routing.model_dump(by_alias=True, exclude_none=True),
            MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
        ))

        extensions = [MESSAGING_EXTENSION_URI_V1]

        message_id = message.id or str(uuid.uuid4())
        user_meta = {k: v for k, v in (metadata or {}).items() if not k.startswith("aion:")} or None
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id,
            role=Role.ROLE_AGENT,
            parts=a2a_parts,
            extensions=extensions,
            metadata=user_meta,
        )
        return [TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
        )]

    def _convert_reaction(self, event: ReactionCustomEvent) -> list[A2AAgentEvent]:
        """Convert a ReactionCustomEvent to an A2A event.

        Produces a TaskArtifactUpdateEvent with a reserved artifact_id so the
        distribution receives the ReactionActionPayload without it being saved
        to task history or artifacts.
        """
        proto_value = struct_pb2.Value()
        json_format.ParseDict(event.payload.model_dump(by_alias=True, exclude_none=True), proto_value)
        return [TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            metadata={MESSAGING_EXTENSION_URI_V1: {"schema": REACTION_ACTION_PAYLOAD_SCHEMA_V1}},
            artifact=Artifact(
                artifact_id=ArtifactId.REACTION.value,
                name=ArtifactName.REACTION.value,
                parts=[Part(data=proto_value)],
            ),
            append=False,
            last_chunk=True,
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
