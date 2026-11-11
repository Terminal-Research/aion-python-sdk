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
from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_end, thinking_delta
from aion.core.a2a.metadata import agent_metadata
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from aion.core.constants import (
    CARDS_EXTENSION_URI_V1,
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

THREAD_STREAM = "aion:thread"
"""Key of the stream ``Thread.reply()`` makes of an async iterator; a model call's key is its message id."""


class LangGraphA2AConverter:
    """Converts LangGraph events directly to A2A protocol events.

    Single converter that handles all LangGraph event types and produces
    A2A events without an intermediate ExecutionEvent representation.

    A task has one streamed answer at a time: ``aion:stream-delta`` is one
    artifact with one open section. Streams that run at once - the model
    calls of parallel nodes or subgraphs, a ``Thread.reply()`` iterator
    beside them - therefore take turns. The stream that started first is
    live and its chunks go out as they come; the chunks of every other
    stream are held, and once the live one has ended the next goes out as a
    section of its own, in the order the streams started. Each answer
    reaches the client whole, and the task keeps the section that ended
    last, as it does after model calls made one after another.
    """

    def __init__(self, task_id: str, context_id: str):
        self._task_id = task_id
        self._context_id = context_id
        # The open section of `aion:stream-delta`, and whether its last chunk
        # already said so.
        self._section_open = False
        self._section_ended = False
        # The live stream: a model call of the graph's "messages" stream, by
        # its message id, or the thread's (THREAD_STREAM); None when no
        # stream is running. A model call's stream is done once its last
        # chunk arrived; the thread's is done with the message that closes it.
        self._live: Optional[str] = None
        self._live_done = False
        # What the streams that wait for the live one sent, by stream, in the
        # order the streams started.
        self._waiting: dict[str, list[Any]] = {}
        # Ids of the messages that ended a section. A node that keeps such a
        # message in the graph's state makes LangGraph send it again on the
        # "messages" stream; that copy is the same answer, not a new reply.
        self._section_closer_ids: set[str] = set()

    def convert(self, event_type: str, event_data: Any) -> list[A2AAgentEvent]:
        """Convert a LangGraph event to zero or more A2A events.

        Args:
            event_type: LangGraph event type (messages, custom, values, updates).
            event_data: LangGraph event data.

        Returns:
            List of A2A events (may be empty for skipped event types).
        """
        if event_type == "messages":
            return self._convert_graph_message(event_data) + self._release_waiting()
        elif event_type == "custom":
            return self._convert_custom(event_data) + self._release_waiting()
        elif event_type in SKIP_EVENTS:
            return []
        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return []

    def close_stream(self) -> list[A2AAgentEvent]:
        """End a section of the streamed answer the run left open.

        A stream still waiting behind one that never ended goes out first,
        each as its own section.
        """
        events = self._close_section()
        while self._waiting:
            events.extend(self._replay_next_waiting())
            events.extend(self._close_section())
        return events

    def _held(self) -> bool:
        """Whether a stream is live and has not ended, so that another has to wait."""
        return self._live is not None and not self._live_done

    def _release_waiting(self) -> list[A2AAgentEvent]:
        """Let the next waiting streams out once the live one has ended.

        A stream that ended while it waited goes out whole and gives way to
        the one after it; one that is still running becomes the live stream.
        """
        events: list[A2AAgentEvent] = []
        while self._waiting and not self._held():
            if self._live is not None and self._live != THREAD_STREAM:
                # The call's own closing message, should LangGraph send one
                # later, is the answer its section already carried.
                self._section_closer_ids.add(self._live)
            events.extend(self._close_section())
            events.extend(self._replay_next_waiting())
        return events

    def _replay_next_waiting(self) -> list[A2AAgentEvent]:
        """Convert what the stream that waited longest sent, as if it arrived now."""
        key = next(iter(self._waiting))
        events: list[A2AAgentEvent] = []
        for item in self._waiting.pop(key):
            if key == THREAD_STREAM:
                events.extend(self._convert_node_event_in_turn(item))
            else:
                events.extend(self._convert_graph_message(item))
        return events

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
        chunk under a new id, after the last chunk of the call before it, is
        where the next model call starts: the earlier section ends, and the
        new call opens a new one. A chunk under a new id while the live
        stream has not ended is a call running beside it: it waits its turn
        (see the class docstring). A finished message
        under the open section's id is that call's own closing message: it
        ends the section, and its text, which the chunks carried already, is
        not sent again. A finished message of a call that did not stream is
        a message.

        ``Thread.reply()`` with an async iterator streams its chunks through
        the "custom" stream and ends the section with the whole message. A
        node that returns that message into the graph's state gets it sent
        once more here, as LangGraph does with every message a node returns;
        a message that already ended a section produces no event. A finished
        message of a call that did not stream, arriving while a stream is
        live, is a message beside it and leaves the section alone.
        """
        if not isinstance(message, AIMessage):
            return []
        is_chunk = isinstance(message, AIMessageChunk)
        if not is_chunk and message.id in self._section_closer_ids:
            return []

        key = message.id or ""
        if key in self._waiting:
            self._waiting[key].append(message)
            return []

        if is_chunk:
            if self._live == key:
                return self._convert_live_chunk(message)
            if self._held():
                self._waiting[key] = [message]
                return []
            events = self._close_section()
            self._live = key
            return events + self._convert_live_chunk(message)

        if self._live == key:
            return self._close_section_with(message)
        if self._held():
            return self._convert_full_message(message)
        events = self._close_section() if self._live is not None else []
        return events + self._convert_full_message(message)

    def _convert_live_chunk(self, message: AIMessageChunk) -> list[A2AAgentEvent]:
        """Send a chunk of the live model call, and note when it is the call's last."""
        events = self._convert_streaming_chunk(message)
        if getattr(message, "chunk_position", None) == "last":
            self._live_done = True
        return events

    def _convert_message(
            self,
            message: AIMessage | AIMessageChunk,
            metadata: dict | None = None,
            routing: MessageActionPayload | None = None,
    ) -> list[A2AAgentEvent]:
        """Convert an AIMessage or AIMessageChunk the thread or a node emitted to A2A events."""
        if isinstance(message, AIMessageChunk):
            return self._convert_streaming_chunk(message, metadata=metadata, routing=routing)
        if self._section_open:
            return self._close_section_with(message, routing=routing)
        return self._convert_full_message(message, metadata=metadata)

    def _convert_streaming_chunk(
            self,
            message: AIMessageChunk,
            metadata: dict | None = None,
            routing: MessageActionPayload | None = None,
    ) -> list[A2AAgentEvent]:
        """Emit a chunk of ``aion:stream-delta`` for a single AIMessageChunk.

        The first chunk of a section starts the artifact over (append=False);
        the rest extend it. A routing target goes out on the section's first
        chunk only. langchain-core marks a model call's last chunk with
        ``chunk_position="last"``: a last chunk with content ends the section
        with ``lastChunk`` set, and an empty one - the usual end of a
        langchain-core stream - is the section's end mark. Any other chunk
        without content produces no event.
        """
        is_last = getattr(message, "chunk_position", None) == "last"
        parts = LcToA2AConverter.from_message(message)
        if not parts:
            if is_last and self._section_open and not self._section_ended:
                self._section_ended = True
                return [stream_delta_end(task_id=self._task_id, context_id=self._context_id)]
            return []

        append = self._section_open
        self._section_open = True
        self._section_ended = is_last
        return [stream_delta_chunk(
            task_id=self._task_id,
            context_id=self._context_id,
            parts=parts,
            append=append,
            last_chunk=is_last,
            metadata=agent_metadata(metadata) or None,
            routing=None if append else routing,
        )]

    def _close_section(self) -> list[A2AAgentEvent]:
        """End the open section of ``aion:stream-delta``, if one is open.

        A section whose last chunk did not say so gets the end mark
        (``stream_delta_end``); its text is not sent again.
        """
        self._live = None
        self._live_done = False
        if not self._section_open:
            return []
        owes_end = not self._section_ended
        self._section_open = False
        self._section_ended = False
        if not owes_end:
            return []
        return [stream_delta_end(task_id=self._task_id, context_id=self._context_id)]

    def _close_section_with(
            self, message: AIMessage, routing: MessageActionPayload | None = None
    ) -> list[A2AAgentEvent]:
        """End the open section with the message that closes it.

        The message's text is the section's text, which its chunks carried
        already; anything else it carries, a file say, goes out as a message,
        where the message's routing target says when it has one.
        """
        events = self._close_section()
        if message.id:
            self._section_closer_ids.add(message.id)
        parts = LcToA2AConverter.from_message(message)
        rest = [part for part in parts if part.WhichOneof("content") != "text"]
        if rest:
            events.extend(self._message_event(
                rest, message_id=message.id, role=self._detect_role(message), routing=routing
            ))
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
            routing: MessageActionPayload | None = None,
    ) -> list[A2AAgentEvent]:
        """A message of the agent's, with its routing target beside its content when it has one."""
        msg = Message(
            context_id=self._context_id,
            task_id=self._task_id,
            message_id=message_id or str(uuid.uuid4()),
            role=role,
            parts=[*parts, routing.to_part()] if routing is not None else parts,
            extensions=[MESSAGING_EXTENSION_URI_V1] if routing is not None else None,
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
            parts.append(routing.to_part())
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
        streaming. An event that arrives while a stream is still running
        comes from a node beside it: a message, card or artifact goes out at
        once and leaves the section alone, and a ``Thread.reply()`` stream
        waits its turn, with everything the thread emits after it, so that
        the thread's own order holds.
        """
        if isinstance(event_data, ThinkingCustomEvent):
            return self._convert_thinking(event_data)

        if isinstance(event_data, ReactionCustomEvent):
            return self._convert_reaction(event_data)

        if THREAD_STREAM in self._waiting:
            self._waiting[THREAD_STREAM].append(event_data)
            return []
        return self._convert_node_event_in_turn(event_data)

    def _convert_node_event_in_turn(self, event_data: SupportedCustomEvents) -> list[A2AAgentEvent]:
        """Convert a node's event, given which stream is live."""
        is_message = isinstance(event_data, MessageCustomEvent)
        is_chunk = is_message and isinstance(event_data.message, AIMessageChunk)

        if self._live == THREAD_STREAM:
            events = self._convert_node_event(event_data)
            if is_message and not is_chunk:
                # The whole message ends the thread's stream, with or
                # without a section its chunks opened.
                self._live = None
                self._live_done = False
            return events

        if is_chunk:
            if self._held():
                self._waiting[THREAD_STREAM] = [event_data]
                return []
            events = self._close_section() if self._live is not None else []
            self._live = THREAD_STREAM
            return events + self._convert_node_event(event_data)

        if self._held():
            if is_message:
                return self._convert_whole_message(event_data)
            return self._convert_node_event(event_data)

        events = self._close_section() if self._live is not None else []
        events.extend(self._convert_node_event(event_data))
        return events

    def _convert_whole_message(self, event: MessageCustomEvent) -> list[A2AAgentEvent]:
        """A message a node emitted that belongs to no section: a reply, routed when it names a target."""
        if event.routing is not None:
            return self._convert_routed_message(event.message, event.routing, metadata=event.metadata)
        return self._convert_full_message(event.message, metadata=event.metadata)

    def _convert_node_event(self, event_data: SupportedCustomEvents) -> list[A2AAgentEvent]:
        if isinstance(event_data, ArtifactCustomEvent):
            # A routing target travels in the artifact, as a data part beside
            # its content, the way a message carries one.
            artifact = event_data.artifact
            if event_data.routing is not None:
                artifact = event_data.routing.route(artifact)
            return [TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=artifact,
                append=event_data.append,
                last_chunk=event_data.is_last_chunk,
                metadata=agent_metadata(event_data.metadata) or None,
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
        an open section ends it: the streamed text is the answer, not a
        message, and the section carries the target instead.
        """
        if isinstance(message, AIMessageChunk) or self._section_open:
            return self._convert_message(message, metadata=metadata, routing=routing)
        return self._convert_routed_message(message, routing, metadata=metadata)

    def _convert_routed_message(
            self,
            message: AIMessage,
            routing: MessageActionPayload,
            metadata: dict | None = None,
    ) -> list[A2AAgentEvent]:
        """A whole message with its routing target beside its content."""
        a2a_parts = LcToA2AConverter.from_message(message)
        if not a2a_parts:
            return []
        return self._message_event(
            a2a_parts,
            message_id=message.id,
            role=Role.ROLE_AGENT,
            metadata=agent_metadata(metadata) or None,
            routing=routing,
        )

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
