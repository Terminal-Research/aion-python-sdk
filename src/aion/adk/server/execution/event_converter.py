"""Converts ADK events directly to A2A protocol events."""

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
from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.authoring.invocation.event_metadata import (
    PublishedArtifact,
    get_aion_output,
    get_aion_routing,
    get_aion_user_metadata,
)
from aion.adk.server.transformers import A2ATransformer
from aion.core.a2a import ArtifactId, ArtifactName
from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_end, thinking_delta
from aion.core.a2a.extensions.messaging import ReactionActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from aion.core.constants import CARDS_EXTENSION_URI_V1, MESSAGING_EXTENSION_URI_V1, REACTION_ACTION_PAYLOAD_SCHEMA_V1
from google.adk.events import Event
from google.protobuf import json_format, struct_pb2

from aion.adk.authoring.transformers import convert_a2a_part_to_genai_part
from aion.adk.server.artifacts.backends.a2a import A2AArtifactService

from .pause import PendingCall

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = logging.getLogger(__name__)


class ADKToA2AEventConverter:
    """Converts ADK events directly to A2A protocol events.

    Handles partial (streaming) and non-partial (complete) ADK events.

    Partial ADK events are the chunks of a section of ``aion:stream-delta``.
    The non-partial event ADK sends after them carries the section's whole
    text again: it ends the section with the end mark, and its text is
    neither sent again nor made a message. A non-partial event that ends no
    section is a message, a ``WORKING`` status update the task keeps in its
    history.

    A task has one streamed answer at a time: ``aion:stream-delta`` is one
    artifact with one open section. Agents that stream at once - the
    sub-agents of a ``ParallelAgent`` - therefore take turns. ADK gives each
    of them a branch of its own, so a stream is told by its events' author
    and branch. The stream that started first is live and its chunks go out
    as they come; every event of another stream is held, and once the live
    section has ended the next stream goes out as a section of its own, in
    the order the streams started. Each answer reaches the client whole, and
    the task keeps the section that ended last, as it does after model calls
    made one after another.
    """

    def __init__(
            self,
            task_id: str,
            context_id: str,
            ctx: AionInvocationContext | None = None,
    ):
        self._task_id = task_id
        self._context_id = context_id
        self._ctx = ctx
        # Whether a section of `aion:stream-delta` is open.
        self._section_open = False
        self._section_ended = False
        # The stream the open section belongs to, by author and branch.
        self._live: tuple | None = None
        # The events of the streams that wait for the live one, by stream, in
        # the order the streams started.
        self._waiting: dict[tuple, list[Event]] = {}

    async def convert(self, adk_event: Event) -> list[AgentEvent]:
        """Convert an ADK event to zero or more A2A events.

        Args:
            adk_event: ADK Event object with content, partial, author fields.

        Returns:
            List of A2A events (may be empty if the event has no content).
        """
        if adk_event is None:
            return []
        events = await self._convert_in_turn(adk_event)
        return events + await self._release_waiting()

    async def _convert_in_turn(self, adk_event: Event) -> list[AgentEvent]:
        """Convert an event, or hold it while another stream's section is open.

        A reaction, a thought and an artifact published as it is are not part
        of an answer and go out at once. A stream that waits keeps all its
        other events in order, the event that ends it included. A whole event
        of an agent that is not streaming goes out beside the open section
        and leaves it alone.
        """
        if adk_event.partial and not adk_event.content:
            return []
        if not adk_event.partial and self._is_beside_the_answer(adk_event):
            return await self._convert_non_partial(adk_event)

        key = (adk_event.author, adk_event.branch)
        if key in self._waiting:
            self._waiting[key].append(adk_event)
            return []

        held = self._live not in (None, key) and self._section_open and not self._section_ended
        if not adk_event.partial:
            return await self._convert_non_partial(adk_event, beside=held)
        if held:
            self._waiting[key] = [adk_event]
            return []
        events = self._close_section() if self._live != key else []
        self._live = key
        return events + self._convert_partial(adk_event)

    @staticmethod
    def _is_beside_the_answer(adk_event: Event) -> bool:
        """Whether the event is a reaction, a thought or an artifact published as it is."""
        output = get_aion_output(adk_event)
        return bool(output) and (output.reaction is not None or bool(output.thinking) or output.published is not None)

    async def _release_waiting(self) -> list[AgentEvent]:
        """Let the next waiting streams out once no section is running.

        A stream that ended while it waited goes out whole and gives way to
        the one after it; one that is still running becomes the live stream.
        """
        events: list[AgentEvent] = []
        while self._waiting and not (self._section_open and not self._section_ended):
            events.extend(self._close_section())
            events.extend(await self._replay_next_waiting())
        return events

    async def _replay_next_waiting(self) -> list[AgentEvent]:
        """Convert the events of the stream that waited longest, as if they arrived now."""
        key = next(iter(self._waiting))
        events: list[AgentEvent] = []
        for adk_event in self._waiting.pop(key):
            events.extend(await self._convert_in_turn(adk_event))
        return events

    def _convert_partial(self, adk_event: Event) -> list[AgentEvent]:
        """Emit a chunk of ``aion:stream-delta`` for a partial (streaming) ADK event.

        The first chunk of a section starts the artifact over (append=False);
        the rest extend it. The agent's metadata from custom_metadata goes on
        the artifact, so the UI can filter or route individual chunks.

        A model's chunks do not say which is the last: intermediate responses
        are partial=True and the section ends with a separate partial=False
        response carrying the aggregated content, which brings the section's
        end mark. A partial event that sets ``turn_complete`` - the agent's
        own generation marking its last chunk - ends the section itself with
        ``lastChunk`` set, as a LangGraph chunk with ``chunk_position="last"``
        does, and an empty one is the end mark.

        A routing target ``Thread.reply`` gives the chunks goes out on the
        section's first chunk.
        """
        is_last = bool(adk_event.turn_complete)
        parts = A2ATransformer.transform_content(adk_event.content) if adk_event.content else []
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
            metadata=get_aion_user_metadata(adk_event),
            routing=None if append else get_aion_routing(adk_event),
        )]

    def _close_section(self) -> list[AgentEvent]:
        """End the open section of ``aion:stream-delta`` with the end mark, if one is open.

        A section whose last chunk did not say so gets the mark
        (``stream_delta_end``); its text is not sent again.
        """
        self._live = None
        if not self._section_open:
            return []
        owes_end = not self._section_ended
        self._section_open = False
        self._section_ended = False
        if not owes_end:
            return []
        return [stream_delta_end(task_id=self._task_id, context_id=self._context_id)]

    def _build_reaction_event(self, reaction: ReactionActionPayload) -> AgentEvent:
        proto_value = struct_pb2.Value()
        json_format.ParseDict(reaction.model_dump(by_alias=True, exclude_none=True), proto_value)
        return TaskArtifactUpdateEvent(
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
        )

    async def _convert_non_partial(self, adk_event: Event, *, beside: bool = False) -> list[AgentEvent]:
        """Convert a complete (non-partial) ADK event to A2A events.

        If the event carries an aion:output hint, routes to the specified
        output - a reaction, a thought, a card - instead of the default path.

        An event that follows the chunks of a section carries the section's
        whole text, which the chunks carried already: it ends the section,
        and the rest of its content, if any, becomes a message. Any other event's
        content becomes a message: a TaskStatusUpdateEvent (state=working)
        the client receives while the task is still running and the task
        keeps in its history. Finally, artifacts from artifact_delta are
        loaded and emitted as TaskArtifactUpdateEvents.

        Args:
            adk_event: The complete event.
            beside: The open section belongs to another agent's stream that
                is still running: the event neither ends it nor repeats its
                text, so all its content is a message.
        """
        results: list[AgentEvent] = []

        output = get_aion_output(adk_event)

        if output and output.reaction is not None:
            return [self._build_reaction_event(output.reaction)]

        if output and output.thinking:
            return self._convert_thinking(adk_event)

        if output and output.published is not None:
            return [self._published_artifact_event(adk_event, output.published)]

        if output and output.card is not None:
            # A card is the agent's own output after the model call it made,
            # so the section that call was streaming has ended.
            if not beside:
                results.extend(self._close_section())
            if output.card.url:
                card = Card(url=output.card.url)
            else:
                card_jsx = adk_event.content and adk_event.content.parts and adk_event.content.parts[0].text
                card = Card(jsx=card_jsx) if card_jsx else None
            if card:
                routing = get_aion_routing(adk_event)
                parts = [build_card_a2a_part(card)]
                extensions = [CARDS_EXTENSION_URI_V1]
                if routing is not None:
                    parts.append(routing.to_part())
                    extensions.append(MESSAGING_EXTENSION_URI_V1)
                msg = Message(
                    context_id=self._context_id,
                    task_id=self._task_id,
                    message_id=str(uuid.uuid4()),
                    role=Role.ROLE_AGENT,
                    parts=parts,
                    extensions=extensions,
                    metadata=get_aion_user_metadata(adk_event),
                )
                results.append(TaskStatusUpdateEvent(
                    task_id=self._task_id,
                    context_id=self._context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
                ))
            return results

        content_parts = A2ATransformer.transform_content(adk_event.content) if adk_event.content else []
        routing = get_aion_routing(adk_event)
        if self._section_open and not beside:
            results.extend(self._close_section())
            content_parts = [part for part in content_parts if part.WhichOneof("content") != "text"]

        if content_parts:
            extensions = []
            if routing is not None:
                content_parts.append(routing.to_part())
                extensions = [MESSAGING_EXTENSION_URI_V1]
            role = Role.ROLE_USER if adk_event.author == "user" else Role.ROLE_AGENT
            msg = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=role,
                parts=content_parts,
                extensions=extensions or None,
                metadata=get_aion_user_metadata(adk_event),
            )
            results.append(TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
            ))

        results.extend(await self._convert_artifact_delta(adk_event))
        return results

    def _convert_thinking(self, adk_event: Event) -> list[AgentEvent]:
        """Carry a thought as one more part of ``aion:thinking-delta``.

        ``emit_thinking()`` and ``Thread.thinking()`` put the thought in the
        event's content. A thought does not end a streaming section: it is
        not the answer.
        """
        parts = A2ATransformer.transform_content(adk_event.content) if adk_event.content else []
        text = "".join(part.text for part in parts if part.WhichOneof("content") == "text")
        if not text:
            return []
        return [thinking_delta(
            task_id=self._task_id,
            context_id=self._context_id,
            text=text,
            metadata=get_aion_user_metadata(adk_event),
        )]

    async def _convert_artifact_delta(self, adk_event: Event) -> list[AgentEvent]:
        """Load artifacts from artifact_delta and emit TaskArtifactUpdateEvents.

        This is ADK's own path, ``save_artifact`` from a tool or a callback.
        An event that also carries an aion:output.artifact hint names the
        artifact_id and name to use instead of a random UUID. A routing
        target under aion:routing goes into the artifact as a data part beside
        its content, the way a message carries one.
        """
        if not adk_event.actions or not adk_event.actions.artifact_delta:
            return []
        if self._ctx.artifact_service is None:
            return []

        output = get_aion_output(adk_event)
        hint = output.artifact if output else None
        routing = get_aion_routing(adk_event)

        results: list[AgentEvent] = []
        for filename, version in adk_event.actions.artifact_delta.items():
            part = await self._ctx.artifact_service.load_artifact(
                app_name=self._ctx.app_name,
                user_id=self._ctx.user_id,
                session_id=self._ctx.session.id,
                filename=filename,
                version=version,
            )
            if part is None:
                logger.warning("Artifact not found: %s v%s", filename, version)
                continue

            a2a_part = A2ATransformer.transform_part(part)
            if a2a_part is None:
                logger.warning("Could not transform artifact part: %s", filename)
                continue

            file_action = hint.file_action if hint else None
            if file_action is not None:
                a2a_part.metadata.update({MESSAGING_EXTENSION_URI_V1: file_action})

            artifact_id = hint.artifact_id if hint else str(uuid.uuid4())
            name = (hint.artifact_name if hint else None) or filename

            artifact_metadata: dict = {"version": str(version)}
            user_meta = get_aion_user_metadata(adk_event)
            if user_meta:
                artifact_metadata.update(user_meta)

            artifact = Artifact(
                artifact_id=artifact_id,
                name=name,
                parts=[a2a_part],
                metadata=artifact_metadata,
                extensions=[MESSAGING_EXTENSION_URI_V1] if file_action is not None else [],
            )
            event = TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=routing.route(artifact) if routing is not None else artifact,
                append=False,
                last_chunk=True,
            )
            results.append(event)
        return results

    async def finalize_stream(self) -> list[AgentEvent]:
        """End a section the agent's run left open.

        Called when the run is over: partial events arrived and no
        non-partial event followed them, so the section gets its end mark
        here. A stream still waiting behind it goes out after it, as a
        section of its own.
        """
        events = self._close_section()
        while self._waiting:
            events.extend(await self._replay_next_waiting())
            events.extend(self._close_section())
        return events

    def generate_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the ADK agent finishes without error.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

    def _published_artifact_event(
            self, adk_event: Event, published: PublishedArtifact
    ) -> TaskArtifactUpdateEvent:
        """The artifact ``emit_artifact`` published, as it is: the event the LangGraph adapter makes of one.

        A routing target travels in the artifact, as a data part beside its
        content; the agent's own metadata for the update rides on the event.
        """
        artifact = json_format.ParseDict(published.artifact, Artifact())
        if not published.append:
            self._remember_published(artifact)
        routing = get_aion_routing(adk_event)
        if routing is not None:
            artifact = routing.route(artifact)
        return TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=artifact,
            append=published.append,
            last_chunk=published.last_chunk,
            metadata=get_aion_user_metadata(adk_event) or None,
        )

    def _remember_published(self, artifact: Artifact) -> None:
        """Let ADK's own tools find a published artifact in this turn, before its task is stored.

        ADK reads an artifact as one ``Part``: the artifact's first part, the
        same one the artifact service reads back from the task later.
        """
        ctx = self._ctx
        service = getattr(ctx, "artifact_service", None)
        if not isinstance(service, A2AArtifactService) or not artifact.name:
            return
        for a2a_part in artifact.parts:
            part = convert_a2a_part_to_genai_part(a2a_part)
            if part is not None:
                service.remember_published(
                    app_name=ctx.app_name,
                    user_id=ctx.user_id,
                    filename=artifact.name,
                    part=part,
                    session_id=ctx.session.id,
                )
                return

    def generate_pause(self, call: PendingCall) -> TaskStatusUpdateEvent:
        """Produce the input_required status of a run that paused on a long-running tool call.

        Shaped as the LangGraph adapter shapes an ``interrupt()``: one text
        part asking the question, and the call's id as ``interruptId``, so a
        client answers either framework the same way.
        """
        logger.info("Paused for input on %s, call id=%s", call.name, call.call_id)
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                message=Message(
                    context_id=self._context_id,
                    task_id=self._task_id,
                    message_id=str(uuid.uuid4()),
                    role=Role.ROLE_AGENT,
                    parts=[Part(text=call.question)],
                    metadata={"interruptId": call.call_id},
                ),
            ),
        )


__all__ = ["ADKToA2AEventConverter"]
