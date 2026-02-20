"""Per-call stream executor for ADK agent.run_async."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent, TextPart
from aion.shared.logging import get_logger
from aion.shared.types import ArtifactId
from google.adk.events import Event

from .event_converter import ADKToA2AEventConverter

logger = get_logger()

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


@dataclass(frozen=True)
class StreamResult:
    """Accumulated state after one ADK stream cycle.

    delta_text — concatenated text extracted from STREAM_DELTA chunks.
        Non-empty only when the agent streamed partial events without a
        subsequent non-partial event to confirm the full message.
    """

    delta_text: str


class ADKStreamExecutor:
    """Executes one run_async cycle against an ADK agent.

    Lifecycle: instantiate > iterate `execute()` > read `result`.
    Created fresh per stream/resume call. Handles session persistence
    and event conversion without knowing about response assembly.
    """

    def __init__(
        self,
        agent: Any,
        session_service: Any,
        converter: ADKToA2AEventConverter,
    ):
        self._agent = agent
        self._session_service = session_service
        self._converter = converter
        self._delta_text: str = ""

    @property
    def result(self) -> StreamResult:
        """Accumulated state. Valid after `execute()` iteration is complete."""
        return StreamResult(delta_text=self._delta_text)

    async def execute(
        self,
        invocation_context: Any,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Run agent.run_async and yield A2A events.

        Persists non-partial events to session and tracks accumulated
        streaming text for finalization.

        Args:
            invocation_context: ADK InvocationContext.
            session: ADK Session for persisting non-partial events.

        Yields:
            A2A AgentEvent objects.
        """
        async for adk_event in self._agent.run_async(invocation_context):
            if isinstance(adk_event, Event):
                self._prepare_event(adk_event, invocation_context)
                await self._session_service.append_event(session, adk_event)

            for a2a_event in self._converter.convert(adk_event):
                self._track(a2a_event)
                yield a2a_event

    def _prepare_event(self, event: Event, ctx: Any) -> None:
        """Stamp context fields and normalize state_delta for serialization."""
        event.invocation_id = ctx.invocation_id
        event.branch = ctx.branch
        event.author = ctx.agent.name

        if event.actions and event.actions.state_delta:
            outbox = event.actions.state_delta.pop("a2a_outbox", None)
            if outbox:
                if isinstance(outbox, (Task, Message)):
                    event.actions.state_delta["a2a_outbox"] = outbox.model_dump()
                else:
                    logger.warning(f"Unexpected a2a_outbox type: {type(outbox)}")

    def _track(self, a2a_event: AgentEvent) -> None:
        """Update internal state based on the outgoing event."""
        if isinstance(a2a_event, TaskArtifactUpdateEvent):
            if a2a_event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value:
                for part in (a2a_event.artifact.parts or []):
                    if isinstance(part.root, TextPart):
                        self._delta_text += part.root.text
            return

        if isinstance(a2a_event, TaskStatusUpdateEvent):
            if a2a_event.status.message is not None:
                self._delta_text = ""


__all__ = ["ADKStreamExecutor", "StreamResult"]
