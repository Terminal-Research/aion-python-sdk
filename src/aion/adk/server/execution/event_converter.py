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
    get_aion_output,
    get_aion_routing,
    get_aion_user_metadata,
)
from aion.adk.server.transformers import A2ATransformer
from aion.core.a2a import ArtifactId, ArtifactName
from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_final, thinking_delta
from aion.core.a2a.extensions.messaging import ReactionActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from aion.core.constants import CARDS_EXTENSION_URI_V1, MESSAGE_ACTION_PAYLOAD_SCHEMA_V1, MESSAGING_EXTENSION_URI_V1, \
    REACTION_ACTION_PAYLOAD_SCHEMA_V1
from google.adk.events import Event
from google.protobuf import json_format, struct_pb2

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = logging.getLogger(__name__)


class ADKToA2AEventConverter:
    """Converts ADK events directly to A2A protocol events.

    Handles partial (streaming) and non-partial (complete) ADK events.

    Partial ADK events are the chunks of a section of ``aion:stream-delta``.
    The non-partial event ADK sends after them carries the section's whole
    text: it ends the section as the artifact's final event, and becomes no
    message. A non-partial event that ends no section is a message, a
    ``WORKING`` status update the task keeps in its history.
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
        self._section_open = False
        self._section_text = ""
        self._section_metadata: dict | None = None

    async def convert(self, adk_event: Event) -> list[AgentEvent]:
        """Convert an ADK event to zero or more A2A events.

        Args:
            adk_event: ADK Event object with content, partial, author fields.

        Returns:
            List of A2A events (may be empty if the event has no content).
        """
        if adk_event is None:
            return []

        if adk_event.partial:
            if not adk_event.content:
                return []
            return self._convert_partial(adk_event)
        else:
            return await self._convert_non_partial(adk_event)

    def _convert_partial(self, adk_event: Event) -> list[AgentEvent]:
        """Emit a chunk of ``aion:stream-delta`` for a partial (streaming) ADK event.

        The first chunk of a section starts the artifact over (append=False);
        the rest extend it. The agent's metadata from custom_metadata goes on
        the artifact, so the UI can filter or route individual chunks.

        No chunk is the last one, and that is the ADK streaming contract
        rather than a missing feature: intermediate responses are partial=True
        and the section ends with a separate partial=False response carrying
        the aggregated content, so at the moment a partial is converted
        nothing says it was the last one. The section's final event follows
        with that response.
        """
        parts = A2ATransformer.transform_content(adk_event.content)
        if not parts:
            return []

        append = self._section_open
        self._section_open = True
        self._section_text += "".join(part.text for part in parts if part.WhichOneof("content") == "text")
        self._section_metadata = get_aion_user_metadata(adk_event)
        return [stream_delta_chunk(
            task_id=self._task_id,
            context_id=self._context_id,
            parts=parts,
            append=append,
            metadata=self._section_metadata,
        )]

    def _close_section(self, text: str | None = None) -> list[AgentEvent]:
        """End the open section of ``aion:stream-delta`` with its whole text, if one is open.

        Args:
            text: The section's whole text as the framework reports it; the
                text of its chunks when it reports none.
        """
        if not self._section_open:
            return []
        final_text = self._section_text if text is None else text
        metadata = self._section_metadata
        self._section_open = False
        self._section_text = ""
        self._section_metadata = None
        if not final_text:
            return []
        return [stream_delta_final(
            task_id=self._task_id,
            context_id=self._context_id,
            text=final_text,
            metadata=metadata,
        )]

    @staticmethod
    def _build_extension_part(data: dict, schema_uri: str) -> Part:
        proto_value = struct_pb2.Value()
        json_format.ParseDict(data, proto_value)
        return Part(
            data=proto_value,
            metadata={MESSAGING_EXTENSION_URI_V1: {"schema": schema_uri}},
        )

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

    async def _convert_non_partial(self, adk_event: Event) -> list[AgentEvent]:
        """Convert a complete (non-partial) ADK event to A2A events.

        If the event carries an aion:output hint, routes to the specified
        output - a reaction, a thought, a card - instead of the default path.

        An event that follows the chunks of a section carries the section's
        whole text: its text becomes the final event of ``aion:stream-delta``,
        and the rest of its content, if any, a message. Any other event's
        content becomes a message: a TaskStatusUpdateEvent (state=working)
        the client receives while the task is still running and the task
        keeps in its history. Finally, artifacts from artifact_delta are
        loaded and emitted as TaskArtifactUpdateEvents.
        """
        results: list[AgentEvent] = []

        output = get_aion_output(adk_event)

        if output and output.reaction is not None:
            return [self._build_reaction_event(output.reaction)]

        if output and output.thinking:
            return self._convert_thinking(adk_event)

        if output and output.card is not None:
            # A card is the agent's own output after the model call it made,
            # so the section that call was streaming has ended.
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
                    parts.append(self._build_extension_part(
                        routing.model_dump(by_alias=True, exclude_none=True),
                        MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
                    ))
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
        if self._section_open:
            text_parts = [part for part in content_parts if part.WhichOneof("content") == "text"]
            results.extend(self._close_section("".join(part.text for part in text_parts) if text_parts else None))
            content_parts = [part for part in content_parts if part.WhichOneof("content") != "text"]

        if content_parts:
            routing = get_aion_routing(adk_event)
            extensions = []
            if routing is not None:
                content_parts.append(self._build_extension_part(
                    routing.model_dump(by_alias=True, exclude_none=True),
                    MESSAGE_ACTION_PAYLOAD_SCHEMA_V1,
                ))
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

        When aion:output.artifact hint is present (set by emit_artifact()), uses
        the provided artifact_id and name instead of generating a random UUID.
        This preserves the identity of explicitly emitted artifacts.
        """
        if not adk_event.actions or not adk_event.actions.artifact_delta:
            return []
        if self._ctx.artifact_service is None:
            return []

        output = get_aion_output(adk_event)
        hint = output.artifact if output else None

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

            event = TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=Artifact(
                    artifact_id=artifact_id,
                    name=name,
                    parts=[a2a_part],
                    metadata=artifact_metadata,
                    extensions=[MESSAGING_EXTENSION_URI_V1] if file_action is not None else [],
                ),
                append=False,
                last_chunk=True,
            )
            results.append(event)
        return results

    def finalize_stream(self) -> list[AgentEvent]:
        """End a section the agent's run left open, with the text of its chunks.

        Called when the run is over: partial events arrived and no
        non-partial event followed them, so the section's final event is
        built from what its chunks said.
        """
        return self._close_section()

    def generate_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the ADK agent finishes without error.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )


__all__ = ["ADKToA2AEventConverter"]
