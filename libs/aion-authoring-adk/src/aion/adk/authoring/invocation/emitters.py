"""Streaming helpers for emitting events from ADK agents.

This module provides helper functions to emit events during ADK agent execution.
All events are emitted via the ContextVar-based ADK event emitter set up by ADKStreamExecutor.

Architecture (see thread_message_concept.md):
  Builders (aion-core)  →  emit_* (here, explicit params)  →  Thread (magic wrapper)

All emit_* functions follow the same pattern:
  - actual data goes in event.content (native ADK)
  - aion:output carries only routing metadata (what to wrap into)
"""

from __future__ import annotations

from a2a.types import Artifact
from aion.adk.authoring.constants import AION_OUTPUT_KEY, AION_ROUTING_KEY
from aion.adk.authoring.transformers import convert_a2a_part_to_genai_part
from aion.core.a2a.enums import ArtifactId, ArtifactName
from aion.core.a2a.extensions.messaging import MessageActionPayload, ReactionActionPayload
from aion.core.agent.invocation.card import Card
from aion.core.logging import get_logger
from google.adk.events import Event, EventActions
from google.genai import types
from .context_vars import EventEmitter
from .event_metadata import AionOutput, ArtifactOutput, CardOutput, ReactionOutput

from .invocation_context import AionInvocationContext

logger = get_logger()


def emit_message(
        emitter: EventEmitter,
        text: str,
        *,
        routing: MessageActionPayload | None = None,
        ephemeral: bool = False,
        metadata: dict | None = None,
) -> None:
    """Emit a text message during ADK agent execution.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        text: Text content to emit
        routing: Optional delivery routing target (MessageActionPayload)
        ephemeral: If True, routes event to EPHEMERAL_MESSAGE artifact —
                   shown in real time, never persisted in task history
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        def my_agent(ctx, emitter):
            emit_message(emitter, "Processing your request...")
            emit_message(emitter, "Searching...", ephemeral=True)
            emit_message(emitter, "Done", metadata={"trace_id": "abc"})
    """
    meta: dict = {}
    if metadata:
        meta.update(metadata)
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    if ephemeral:
        meta[AION_OUTPUT_KEY] = AionOutput(
            artifact=ArtifactOutput(
                artifact_id=ArtifactId.EPHEMERAL_MESSAGE.value,
                artifact_name=ArtifactName.EPHEMERAL_MESSAGE.value,
            )
        ).model_dump(exclude_none=True)

    emitter(Event(
        author="agent",
        content=types.Content(parts=[types.Part(text=text)], role="model"),
        partial=False,
        custom_metadata=meta or None,
    ))


def emit_card(
        emitter: EventEmitter,
        card: Card,
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> None:
    """Emit a card message during ADK agent execution.

    Produces a TaskStatusUpdateEvent whose message contains a card file part
    and extensions=[CardsURI]. When routing is provided, a DataPart with
    MessageActionPayload is also attached.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        card: Card instance (jsx or url)
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata forwarded to A2A Message.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.agent.invocation.card import Card

        def my_agent(ctx, emitter):
            emit_card(emitter, Card(jsx="<Card><Text>Hello</Text></Card>"))
            emit_card(emitter, Card(jsx="<Card/>"), metadata={"template": "summary"})
    """
    meta = dict(metadata) if metadata else {}

    if card.url:
        meta[AION_OUTPUT_KEY] = AionOutput(card=CardOutput(url=card.url)).model_dump(exclude_none=True)
        content = types.Content(parts=[], role="model")
    else:
        meta[AION_OUTPUT_KEY] = AionOutput(card=CardOutput()).model_dump(exclude_none=True)
        content = types.Content(parts=[types.Part(text=card.jsx)], role="model")

    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    emitter(Event(
        author="agent",
        content=content,
        partial=False,
        custom_metadata=meta,
    ))


async def emit_artifact(
        emitter: EventEmitter,
        ctx: AionInvocationContext,
        artifact: Artifact,
        *,
        routing: MessageActionPayload | None = None,
        metadata: dict | None = None,
) -> None:
    """Emit a pre-built artifact during ADK agent execution.

    Saves each artifact part to artifact_service (which handles bytes→URL
    upload via FileUploadManager when configured), then emits an ADK Event
    with EventActions(artifact_delta=...) — the standard ADK artifact path.
    The aion:output hint carries artifact_id and name so the converter can
    emit a properly identified TaskArtifactUpdateEvent.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        ctx: ADK InvocationContext from the invocation ContextVar
        artifact: Pre-built a2a.types.Artifact to emit
        routing: Optional delivery routing target (MessageActionPayload)
        metadata: Optional user-defined metadata merged into A2A Artifact.metadata.
                  Keys must not start with "aion:" (reserved for service use).

    Example:
        from aion.core.a2a import file_artifact, data_artifact

        async def my_agent(ctx, emitter):
            await emit_artifact(emitter, ctx, file_artifact(url="https://example.com/r.pdf", mime_type="application/pdf"))
            await emit_artifact(emitter, ctx, data_artifact({"score": 42}, name="result"), metadata={"owner": "agent-x"})
    """
    if not isinstance(ctx, AionInvocationContext) or ctx.artifact_service is None:
        logger.warning(
            "emit_artifact: artifact_service not available — event not emitted for '%s'.",
            artifact.artifact_id,
        )
        return

    if len(artifact.parts) > 1:
        logger.warning(
            "emit_artifact: multi-part artifacts are not supported — event not emitted for '%s' (%d parts).",
            artifact.artifact_id,
            len(artifact.parts),
        )
        return

    filename = artifact.name or artifact.artifact_id
    version = None

    for part in artifact.parts:
        genai_part = convert_a2a_part_to_genai_part(part)
        if genai_part is None:
            logger.warning(
                "emit_artifact: could not convert part in artifact '%s' — skipped.",
                artifact.artifact_id,
            )
            continue
        version = await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename=filename,
            artifact=genai_part,
        )

    if version is None:
        logger.warning(
            "emit_artifact: no parts saved for artifact '%s' — event not emitted.",
            artifact.artifact_id,
        )
        return

    meta = dict(metadata) if metadata else {}
    meta[AION_OUTPUT_KEY] = AionOutput(
        artifact=ArtifactOutput(
            artifact_id=artifact.artifact_id,
            artifact_name=artifact.name,
        )
    ).model_dump(exclude_none=True)
    if routing is not None:
        meta[AION_ROUTING_KEY] = routing.model_dump(by_alias=True, exclude_none=True)

    emitter(Event(
        author="agent",
        content=None,
        partial=False,
        actions=EventActions(artifact_delta={filename: version}),
        custom_metadata=meta,
    ))


def emit_reaction(
        emitter: EventEmitter,
        payload: ReactionActionPayload,
) -> None:
    """Emit a reaction action event during ADK agent execution.

    Instructs the distribution to add or remove a reaction on an existing provider message.
    Produces a TaskArtifactUpdateEvent with artifact_id ``aion:reaction``.

    Args:
        emitter: ADK event emitter callable from the invocation ContextVar
        payload: Reaction action to perform

    Example:
        from aion.core.a2a.extensions.messaging import ReactionActionPayload

        def my_agent(ctx, emitter):
            emit_reaction(emitter, ReactionActionPayload(
                context_id="C06ROOM123",
                message_id="1728162300.551219",
                reaction_key="thumbsup",
                operation="add",
            ))
    """
    emitter(Event(
        author="agent",
        content=None,
        partial=False,
        custom_metadata={
            AION_OUTPUT_KEY: AionOutput(
                reaction=ReactionOutput(
                    context_id=payload.context_id,
                    message_id=payload.message_id,
                    reaction_key=payload.reaction_key,
                    operation=payload.operation,
                    display_value=payload.display_value,
                )
            ).model_dump(exclude_none=True)
        },
    ))
