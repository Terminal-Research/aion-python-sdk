"""Converts ADK events directly to A2A protocol events."""

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
from aion.adk.authoring.invocation.event_metadata import (
    AionOutput,
    ReactionOutput,
    get_aion_output,
    get_aion_routing,
    get_aion_user_metadata,
)
from aion.core.agent.invocation.card import Card
from aion.core.constants import CARDS_EXTENSION_URI_V1, MESSAGE_ACTION_PAYLOAD_SCHEMA_V1, MESSAGING_EXTENSION_URI_V1, REACTION_ACTION_PAYLOAD_SCHEMA_V1
from aion.core.logging import get_logger
from aion.core.a2a import ArtifactId, ArtifactName
from aion.core.agent.invocation.card.utils import build_card_a2a_part
from google.adk.events import Event
from google.protobuf import json_format, struct_pb2

from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.server.transformers import A2ATransformer
from aion.server.files.storage import FileUploadManager

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()


class ADKToA2AEventConverter:
    """Converts ADK events directly to A2A protocol events.

    Handles partial (streaming) and non-partial (complete) ADK events.

    Partial ADK events are emitted as STREAM_DELTA artifact updates for live
    display. Non-partial events close the stream and emit a durable
    TaskStatusUpdateEvent with state=working.
    """

    def __init__(
        self,
        task_id: str,
        context_id: str,
        ctx: AionInvocationContext | None = None,
        file_uploader: FileUploadManager | None = None,
    ):
        self._task_id = task_id
        self._context_id = context_id
        self._ctx = ctx
        self._file_uploader = file_uploader
        self._streaming_started = False
        self._stream_user_metadata: dict | None = None

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
        """Emit a STREAM_DELTA artifact update for a partial (streaming) ADK event.

        The first chunk opens the artifact (append=False); subsequent chunks
        use append=True. All partial events carry last_chunk=False because the
        stream is only closed when the final non-partial event arrives.
        User metadata from custom_metadata is merged into the artifact metadata
        so the UI can filter or route individual chunks.
        """
        parts = A2ATransformer.transform_content(adk_event.content)
        if not parts:
            return []

        append = self._streaming_started
        if not self._streaming_started:
            self._streaming_started = True

        user_meta = get_aion_user_metadata(adk_event)
        if user_meta:
            self._stream_user_metadata = user_meta

        artifact_metadata = {"status": "active", "status_reason": "chunk_streaming"}
        user_meta = get_aion_user_metadata(adk_event)
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
            last_chunk=False,
        )]

    def _close_stream_delta(self) -> TaskArtifactUpdateEvent | None:
        """Close the open STREAM_DELTA artifact if streaming was active.

        User metadata captured from the first chunk is forwarded to the
        close event so consumers can correlate it with the stream they opened.
        """
        if not self._streaming_started:
            return None

        self._streaming_started = False
        close_metadata: dict = {"status": "completed"}
        if self._stream_user_metadata:
            close_metadata.update(self._stream_user_metadata)
        self._stream_user_metadata = None

        return TaskArtifactUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            artifact=Artifact(
                artifact_id=ArtifactId.STREAM_DELTA.value,
                name=ArtifactName.STREAM_DELTA.value,
                parts=[],
                metadata=close_metadata,
            ),
            append=True,
            last_chunk=True,
        )

    @staticmethod
    def _build_extension_part(data: dict, schema_uri: str) -> Part:
        proto_value = struct_pb2.Value()
        json_format.ParseDict(data, proto_value)
        return Part(
            data=proto_value,
            metadata={MESSAGING_EXTENSION_URI_V1: {"schema": schema_uri}},
        )

    def _build_reaction_event(self, reaction: ReactionOutput) -> AgentEvent:
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
        artifact type instead of the default durable message path.

        If streaming was active, the STREAM_DELTA is closed first with an
        empty last_chunk=True event. Each file part is then emitted as a
        standalone TaskArtifactUpdateEvent with a unique artifact id. All
        remaining text parts are grouped into a single TaskStatusUpdateEvent
        (state=working) so the client receives the durable message while the
        task is still running. Finally, artifacts from artifact_delta are
        loaded and emitted as TaskArtifactUpdateEvents.
        """
        results: list[AgentEvent] = []

        output = get_aion_output(adk_event)

        if output and output.reaction is not None:
            return [self._build_reaction_event(output.reaction)]

        if output and output.card is not None:
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

        if close_event := self._close_stream_delta():
            results.append(close_event)

        if adk_event.content:
            content_parts = A2ATransformer.transform_content(adk_event.content)

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

            if self._file_uploader is not None and a2a_part.raw:
                url = self._file_uploader.schedule(
                    data=a2a_part.raw,
                    mime_type=a2a_part.media_type or "application/octet-stream",
                    context_id=self._context_id,
                )
                a2a_part = Part(url=url, media_type=a2a_part.media_type, filename=a2a_part.filename)

            artifact_id = hint.artifact_id if hint else str(uuid.uuid4())
            name = (hint.artifact_name if hint else None) or filename

            artifact_metadata: dict = {"version": str(version)}
            user_meta = get_aion_user_metadata(adk_event)
            if user_meta:
                artifact_metadata.update(user_meta)

            results.append(TaskArtifactUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact=Artifact(
                    artifact_id=artifact_id,
                    name=name,
                    parts=[a2a_part],
                    metadata=artifact_metadata,
                ),
                append=False,
                last_chunk=True,
            ))
        return results

    def finalize_stream(self, delta_text: str) -> list[AgentEvent]:
        """Close any open STREAM_DELTA and emit accumulated text as working status.

        Called when the agent stream ends with active streaming — partial events
        arrived but no closing non-partial event followed. Handles the edge case
        where the last ADK event was partial, leaving an open stream artifact and
        unconfirmed text that needs to be emitted before the terminal event.
        """
        results: list[AgentEvent] = []

        if close_event := self._close_stream_delta():
            results.append(close_event)

        if delta_text:
            msg = Message(
                context_id=self._context_id,
                task_id=self._task_id,
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=delta_text)],
            )
            results.append(TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=msg),
            ))

        return results

    def generate_complete(self) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=completed.

        Called after the ADK agent finishes without error.
        """
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

    def generate_error(self, error: str, error_type: str) -> TaskStatusUpdateEvent:
        """Produce a final TaskStatusUpdateEvent with state=failed and log the error.

        Called when the ADK agent raises an unhandled exception. The error
        details are logged at ERROR level but are not forwarded to the client.
        """
        logger.error(f"Execution error: {error}, type={error_type}")
        return TaskStatusUpdateEvent(
            task_id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
        )


__all__ = ["ADKToA2AEventConverter"]
