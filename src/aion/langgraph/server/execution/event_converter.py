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
from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_final, thinking_delta
from aion.core.a2a.metadata import agent_metadata
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from aion.core.constants import (
    CARDS_EXTENSION_URI_V1,
    MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
    MESSAGING_EXTENSION_URI_V1,
    REACTION_ACTION_PAYLOAD_SCHEMA_V1,
)
from aion.langgraph.authoring.events.custom_events import (
    ArtifactCustomEvent,
    CardCustomEvent,
    MessageCustomEvent,
    ReactionCustomEvent,
    ThinkingCustomEvent,
)
from collections.abc import Sequence
from google.protobuf import json_format, struct_pb2
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.types import Interrupt
from typing import Any, Optional

from ..converters.lc_to_a2a import LcToA2AConverter

LangChainAgentMessage = AIMessage | AIMessageChunk
SupportedCustomEvents = (
    ArtifactCustomEvent | CardCustomEvent | MessageCustomEvent | ReactionCustomEvent | ThinkingCustomEvent
)

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
        # The open section of `aion:stream-delta`: its text so far and the
        # agent's metadata for it. The section comes from the graph's
        # "messages" stream, a model call identified by `_graph_stream_id`,
        # or from the thread, when `_graph_stream_id` is None.
        self._section_open = False
        self._section_text = ""
        self._section_metadata: dict | None = None
        self._graph_stream_id: Optional[str] = None

    def convert(self, event_type: str, event_data: Any) -> list[A2AAgentEvent]:
        """Convert a LangGraph event to zero or more A2A events.

        Args:
            event_type: LangGraph event type (messages, custom, values, updates).
            event_data: LangGraph event data.

        Returns:
            List of A2A events (may be empty for skipped event types).
        """
        if event_type == "messages":
            return self._convert_graph_message(event_data)
        elif event_type == "custom":
            return self._convert_custom(event_data)
        elif event_type in SKIP_EVENTS:
            return []
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return []

    def close_stream(self) -> list[A2AAgentEvent]:
        """End a section of the streamed answer the run left open, with the text of its chunks."""
        return self._close_section()

    def _convert_graph_message(self, message: Any) -> list[A2AAgentEvent]:
        """Convert a message from the graph's "messages" stream.

        That stream carries every message a node produced: model output, and
        also the ToolMessage a tool node returns, a HumanMessage a node
        appends, and so on. Only AI messages are the agent speaking; the
        others are the conversation's bookkeeping and are not sent. An AI
        message with nothing to show - a model turn that only calls a tool -
        produces no event either.

        The chunks of a model call are a section of ``aion:stream-delta``.
        Every call to a model streams under its own message id, and LangGraph
        does not repeat the finished message once its chunks went out. So a
        chunk under a new id is where the previous model call ended: its
        section ends with the text of its chunks, and the new call opens a
        new one. A finished message under the open section's id is that
        call's own closing message: its text ends the section. A finished
        message of a call that did not stream is a message.
        """
        if not isinstance(message, AIMessage):
            return []

        events: list[A2AAgentEvent] = []
        if self._graph_stream_id is not None and message.id != self._graph_stream_id:
            events.extend(self._close_section())

        if isinstance(message, AIMessageChunk):
            if self._graph_stream_id is None:
                events.extend(self._close_section())
                self._graph_stream_id = message.id
            events.extend(self._convert_streaming_chunk(message))
            return events

        if self._graph_stream_id is not None:
            events.extend(self._close_section_with(message))
        else:
            events.extend(self._convert_full_message(message))
        return events

    def _convert_message(self, message: AIMessage | AIMessageChunk, metadata: dict | None = None) -> list[
        A2AAgentEvent]:
        """Convert an AIMessage or AIMessageChunk the thread or a node emitted to A2A events."""
        if isinstance(message, AIMessageChunk):
            return self._convert_streaming_chunk(message, metadata=metadata)
        if self._section_open:
            return self._close_section_with(message)
        return self._convert_full_message(message, metadata=metadata)

    def _convert_streaming_chunk(self, message: AIMessageChunk, metadata: dict | None = None) -> list[A2AAgentEvent]:
        """Emit a chunk of ``aion:stream-delta`` for a single AIMessageChunk.

        Chunks without content produce no event, so every emitted update carries
        at least one Part (langchain-core ends a stream with an empty
        chunk_position="last" chunk). The first chunk of a section starts the
        artifact over (append=False); the rest extend it. No chunk is the
        section's last: the section ends with an event of its own.
        """
        parts = LcToA2AConverter.from_message(message)
        if not parts:
            return []

        append = self._section_open
        self._section_open = True
        self._section_text += "".join(part.text for part in parts if part.WhichOneof("content") == "text")
        self._section_metadata = agent_metadata(metadata) or None
        return [stream_delta_chunk(
            task_id=self._task_id,
            context_id=self._context_id,
            parts=parts,
            append=append,
            metadata=self._section_metadata,
        )]

    def _close_section(self, text: str | None = None) -> list[A2AAgentEvent]:
        """End the open section of ``aion:stream-delta`` with its whole text, if one is open.

        Args:
            text: The section's whole text from the message that closes it;
                the text of its chunks when there is no such message.
        """
        if not self._section_open:
            self._graph_stream_id = None
            return []
        final_text = self._section_text if text is None else text
        metadata = self._section_metadata
        self._section_open = False
        self._section_text = ""
        self._section_metadata = None
        self._graph_stream_id = None
        if not final_text:
            return []
        return [stream_delta_final(
            task_id=self._task_id,
            context_id=self._context_id,
            text=final_text,
            metadata=metadata,
        )]

    def _close_section_with(self, message: AIMessage) -> list[A2AAgentEvent]:
        """End the open section with the message that closes it.

        The message's text is the section's whole text; anything else it
        carries, a file say, goes out as a message.
        """
        parts = LcToA2AConverter.from_message(message)
        text_parts = [part for part in parts if part.WhichOneof("content") == "text"]
        events = self._close_section("".join(part.text for part in text_parts) if text_parts else None)
        rest = [part for part in parts if part.WhichOneof("content") != "text"]
        if rest:
            events.extend(self._message_event(rest, message_id=message.id, role=self._detect_role(message)))
        return events

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
        return self._message_event(
            a2a_parts,
            message_id=message.id,
            role=self._detect_role(message),
            metadata=agent_metadata(metadata) or None,
        )

    def _message_event(
            self,
            parts: list[Part],
            *,
            message_id: str | None,
            role: Role,
            metadata: dict | None = None,
    ) -> list[A2AAgentEvent]:
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id or str(uuid.uuid4()),
            role=role,
            parts=parts,
            metadata=metadata,
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
        user_meta = agent_metadata(metadata) or None
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
        CardCustomEvent (card message), MessageCustomEvent (message or chunk
        of the streamed answer), ReactionCustomEvent (reaction action) and
        ThinkingCustomEvent (one thought). Unknown types are logged and
        silently dropped.

        A node emits after the model call it made has ended, so any event
        but a thought or a reaction first ends the section that call was
        streaming.
        """
        if isinstance(event_data, ThinkingCustomEvent):
            return self._convert_thinking(event_data)

        if isinstance(event_data, ReactionCustomEvent):
            return self._convert_reaction(event_data)

        events = self._close_section() if self._graph_stream_id is not None else []
        events.extend(self._convert_node_event(event_data))
        return events

    def _convert_node_event(self, event_data: SupportedCustomEvents) -> list[A2AAgentEvent]:
        if isinstance(event_data, ArtifactCustomEvent):
            event_metadata: dict = {}
            if event_data.routing is not None:
                event_metadata[MESSAGING_EXTENSION_URI_V1] = event_data.routing.model_dump(by_alias=True, exclude_none=True)
            if event_data.metadata:
                user_meta = agent_metadata(event_data.metadata)
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
            if event_data.routing is not None:
                return self._convert_message_with_routing(event_data.message, event_data.routing,
                                                          metadata=event_data.metadata)
            return self._convert_message(event_data.message, metadata=event_data.metadata)

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

        A chunk is a chunk of the streamed answer, and a message that closes
        an open section ends it, as without a target: the streamed text is
        the answer, not a message to deliver.
        """
        if isinstance(message, AIMessageChunk) or self._section_open:
            return self._convert_message(message, metadata=metadata)

        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []

        a2a_parts.append(self._build_extension_part(
            routing.model_dump(by_alias=True, exclude_none=True),
            MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
        ))

        extensions = [MESSAGING_EXTENSION_URI_V1]

        message_id = message.id or str(uuid.uuid4())
        user_meta = agent_metadata(metadata) or None
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

    def _convert_thinking(self, event: ThinkingCustomEvent) -> list[A2AAgentEvent]:
        """Carry a thought as one more part of ``aion:thinking-delta``."""
        if not event.text:
            return []
        return [thinking_delta(
            task_id=self._task_id,
            context_id=self._context_id,
            text=event.text,
            metadata=agent_metadata(event.metadata) or None,
        )]

    def _convert_reaction(self, event: ReactionCustomEvent) -> list[A2AAgentEvent]:
        """Convert a ReactionCustomEvent to an A2A event.

        Produces a TaskArtifactUpdateEvent with the reserved artifact_id
        ``aion:reaction``, which carries the ReactionActionPayload to the
        distribution. The task keeps the latest reaction; it is not a result.
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

    def convert_interrupt(self, interrupts: Sequence[Interrupt]) -> TaskStatusUpdateEvent:
        """Produce an input_required status event from the graph's pending interrupts.

        Uses the first interrupt to build the prompt message and attaches its id
        as message metadata so the client can resume the correct thread.

        Args:
            interrupts: What the graph stopped on, as LangGraph reports it in
                ``StateSnapshot.interrupts``.
        """
        message: Optional[Message] = None
        interrupt_id: Optional[str] = None

        if interrupts:
            interrupt = interrupts[0]
            interrupt_id = interrupt.id
            message = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=self._interrupt_prompt(interrupt.value))],
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

    @staticmethod
    def _interrupt_prompt(value: Any) -> str:
        """The question the client sees for an ``interrupt()`` value.

        A string is the question itself. A dict names it under ``"prompt"``.
        Any other value - or a dict without a prompt - asks generically.
        """
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and value.get("prompt"):
            return value["prompt"]
        return "Agent requires input"

    def convert_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the LangGraph graph finishes without error or interrupt.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
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
