"""Per-call stream executor for ADK agent.run_async."""

import logging
import asyncio
import contextlib
from collections.abc import AsyncIterator, AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.adk.authoring.invocation.context_vars import (
    reset_adk_ctx,
    reset_adk_emitter,
    set_adk_ctx,
    set_adk_emitter,
)
from aion.core.a2a import ArtifactId, A2AOutbox
from google.adk.events import Event

from .event_converter import ADKToA2AEventConverter
from .event_queue import ADKEventConsumer, ADKEventQueue

logger = logging.getLogger(__name__)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


@dataclass(frozen=True)
class ADKStreamResult:
    """Accumulated state after one ADK stream cycle.

    delta_text — concatenated text extracted from STREAM_DELTA chunks.
        Non-empty only when the agent streamed partial events without a
        subsequent non-partial event to confirm the full message.
    outbox — the last `a2a_outbox` an event's state_delta carried during
        this cycle, serialized, or None when none did. Taken from the
        cycle's own events rather than from the session state: the session
        keeps the key across turns, and the next turn in the context would
        read it back.
    """

    delta_text: str
    outbox: Any = None


class ADKStreamExecutor:
    """Executes one run_async cycle against an ADK agent.

    Lifecycle: instantiate > iterate execute() > read result.
    Created fresh per stream/resume call.

    Architecture mirrors the a2a-sdk EventQueue / EventConsumer split:

      ADKEventQueue    — single event bus for the invocation.
      ADKEventConsumer — reads the bus until the agent finishes.

    Two sources write into the queue:
      - agent.run_async()  via _run_agent (agent task)
      - thread.reply() etc via ContextVar emitter → queue.enqueue_event

    The ContextVar emitter is set before the agent task is created so the
    task inherits the correct context snapshot.
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
        self._outbox: Any = None
        self._persisted: set[str] = set()

    @property
    def result(self) -> ADKStreamResult:
        """Accumulated state. Valid after execute() iteration is complete."""
        return ADKStreamResult(delta_text=self._delta_text, outbox=self._outbox)

    async def execute(
        self,
        invocation_context: Any,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Run agent and yield A2A events.

        Args:
            invocation_context: ADK InvocationContext.
            session: ADK Session for persisting events.

        Yields:
            A2A AgentEvent objects.
        """
        async with self._managed_invocation(invocation_context, session) as consumer:
            async for event in consumer.consume_all():
                async for a2a_event in self._process_event(event, invocation_context, session):
                    yield a2a_event

    @asynccontextmanager
    async def _managed_invocation(
        self,
        invocation_context: Any,
        session: Any,
    ) -> AsyncGenerator[ADKEventConsumer, None]:
        """Set up the event queue, emitter, and agent task for one invocation.

        Yields the consumer so the caller can iterate events. Guarantees
        cleanup — queue close, emitter reset, task cancellation — on exit
        regardless of how the caller exits (normal, exception, or early break).
        """
        queue = ADKEventQueue()
        consumer = ADKEventConsumer(queue)

        # Emitter and ctx must be set BEFORE create_task so the task inherits
        # the ContextVar snapshot that includes both references.
        emitter_token = set_adk_emitter(queue.enqueue_event)
        ctx_token = set_adk_ctx(invocation_context)

        agent_task = asyncio.create_task(
            self._run_agent(queue, invocation_context, session)
        )
        agent_task.add_done_callback(consumer.agent_task_callback)

        try:
            yield consumer
        finally:
            if not queue.is_closed():
                queue.close()

            reset_adk_emitter(emitter_token)
            reset_adk_ctx(ctx_token)
            if not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task

    async def _run_agent(
        self,
        queue: ADKEventQueue,
        invocation_context: Any,
        session: Any,
    ) -> None:
        """Drive agent.run_async(), persisting each event before forwarding it.

        The agent does not resume until its event is in the session, which is
        what ADK's own Runner guarantees: the next model call builds its
        request from the session, and a function response still waiting in
        the queue would be missing from it - the model would ask for the
        same tool again.
        """
        try:
            async for event in self._agent.run_async(invocation_context):
                await self._persist(event, invocation_context, session)
                queue.enqueue_event(event)
        except Exception as exc:
            logger.error("Agent run_async failed: %s", exc, exc_info=True)
            queue.enqueue_error(exc)
        finally:
            queue.close()

    async def _process_event(
        self,
        event: Event,
        invocation_context: Any,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Persist if not yet persisted, then convert, track and yield A2A events."""
        if event.id not in self._persisted:
            await self._persist(event, invocation_context, session)

        if not event.partial and event.content:
            for part in event.content.parts:
                fc = getattr(part, "function_call", None)
                if fc:
                    logger.info("Tool call: %s", fc.name)
                    continue
                fr = getattr(part, "function_response", None)
                if fr:
                    logger.debug("Tool response: %s", fr.name)

        for a2a_event in await self._converter.convert(event):
            self._track(a2a_event)
            yield a2a_event

    async def _persist(self, event: Event, invocation_context: Any, session: Any) -> None:
        """Stamp one event and append it to the session, once.

        Events from agent.run_async() are persisted by the agent task before
        they are forwarded; events the thread emits reach the queue directly
        and are persisted when the consumer takes them.
        """
        # Looked up before stamping, which drops an outbox written as None:
        # that write still clears one made earlier in the same run.
        wrote_outbox = event.actions is not None and "a2a_outbox" in event.actions.state_delta
        self._stamp_event(event, invocation_context)
        if wrote_outbox:
            self._outbox = event.actions.state_delta.get("a2a_outbox")
        await self._session_service.append_event(session, event)
        self._persisted.add(event.id)

    @staticmethod
    def _stamp_event(event: Event, ctx: Any) -> None:
        """Stamp invocation metadata and normalize state_delta for serialization."""
        event.invocation_id = ctx.invocation_id
        event.branch = ctx.branch
        event.author = ctx.agent.name

        if event.actions and event.actions.state_delta:
            outbox = event.actions.state_delta.pop("a2a_outbox", None)
            if outbox:
                if isinstance(outbox, A2AOutbox):
                    event.actions.state_delta["a2a_outbox"] = outbox.model_dump()
                else:
                    logger.warning("Unexpected a2a_outbox type: %s", type(outbox))

    def _track(self, a2a_event: AgentEvent) -> None:
        """Maintain delta_text accumulator from outgoing A2A events."""
        if isinstance(a2a_event, TaskArtifactUpdateEvent):
            if a2a_event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value:
                for part in a2a_event.artifact.parts:
                    if part.text:
                        self._delta_text += part.text
            return

        if isinstance(a2a_event, TaskStatusUpdateEvent):
            if a2a_event.status.message is not None:
                self._delta_text = ""


__all__ = ["ADKStreamExecutor", "ADKStreamResult"]
