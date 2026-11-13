"""Per-call stream executor: one run of ADK's Runner, read as A2A events."""

import logging
import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator, AsyncGenerator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.adk.authoring.invocation.event_metadata import stays_out_of_session
from aion.adk.authoring.invocation.context_vars import (
    reset_adk_emitter,
    set_adk_ctx,
    set_adk_emitter,
)
from aion.core.a2a import A2AOutbox
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types

from .event_converter import ADKToA2AEventConverter
from .event_queue import ADKEventConsumer, ADKEventQueue
from .pause import PendingCall, paused_on
from .runner import RUN_CONFIG, running_turn

logger = logging.getLogger(__name__)

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent


@dataclass(frozen=True)
class ErrorResponse:
    """The error an event of the run reported (``Event.error_code``, ``Event.error_message``).

    ADK's models report a response that was blocked, cut short or malformed
    this way instead of raising: the event carries the code - a finish or
    block reason - beside whatever content arrived before it, or alone.

    code — the event's ``error_code``, an enum member or a string, or None.
    message — the event's ``error_message``, or None.
    """

    code: Any = None
    message: Optional[str] = None

    @property
    def reason(self) -> str:
        """The sentence the failed task gives its caller."""
        code = getattr(self.code, "name", None) or (str(self.code) if self.code else "")
        if code and self.message:
            return f"The agent reported an error ({code}): {self.message}"
        return f"The agent reported an error: {code or self.message}"


@dataclass(frozen=True)
class ADKStreamResult:
    """Accumulated state after one ADK stream cycle.

    outbox — the last `a2a_outbox` an event's state_delta carried during
        this cycle, serialized, or None when none did. Taken from the
        cycle's own events rather than from the session state: the session
        keeps the key across turns, and the next turn in the context would
        read it back.
    paused_on — the long-running tool call the run paused on, or None
        when the run went to its end.
    error — the error the run's events reported, the last one when several
        did, or None.
    """

    outbox: Any = None
    paused_on: Optional[PendingCall] = None
    error: Optional[ErrorResponse] = None


class ADKStreamExecutor:
    """Executes one run of ADK's Runner and reads it as A2A events.

    Lifecycle: instantiate > iterate execute() > read result.
    Created fresh per stream/resume call.

    The Runner does what it does under any ADK application: it picks the
    agent that answers, resumes a paused invocation, writes the user's
    message and each of the agent's events to the session before the agent
    goes on, and drives the agent. This class is the turn the Runner's
    services report to (``runner.Turn``): it takes the invocation context
    the Runner created and decides what a session keeps.

    Architecture mirrors the a2a-sdk EventQueue / EventConsumer split:

      ADKEventQueue    — single event bus for the invocation.
      ADKEventConsumer — reads the bus until the run finishes.

    Two sources write into the queue:
      - Runner.run_async()  via _run_agent (agent task)
      - thread.reply() etc  via ContextVar emitter → _emit

    The emitter and the turn are set before the agent task is created so the
    task inherits a context that holds both.
    """

    def __init__(
        self,
        runner: Runner,
        converter: ADKToA2AEventConverter,
    ):
        self._runner = runner
        self._converter = converter
        self._ctx: Any = None
        self._outbox: Any = None
        self._thread_events: set[str] = set()
        self._last_agent_events: deque[Event] = deque(maxlen=2)
        self._error: Optional[ErrorResponse] = None

    @property
    def result(self) -> ADKStreamResult:
        """Accumulated state. Valid after execute() iteration is complete."""
        return ADKStreamResult(
            outbox=self._outbox,
            paused_on=paused_on(list(self._last_agent_events)),
            error=self._error,
        )

    @property
    def session(self) -> Any:
        """The session the Runner ran on, or None when the run never started."""
        return self._ctx.session if self._ctx is not None else None

    async def execute(
        self,
        *,
        user_id: str,
        session_id: str,
        new_message: types.Content,
        invocation_id: Optional[str] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the Runner on one message and yield A2A events.

        Args:
            user_id: The session's owner.
            session_id: The session of the context.
            new_message: What the user sent, or the function response that
                answers a paused call.
            invocation_id: The invocation a paused call belongs to, to
                resume it; None for a new invocation.

        Yields:
            A2A AgentEvent objects.
        """
        run = self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
            invocation_id=invocation_id,
            run_config=RUN_CONFIG,
        )
        async with self._managed_invocation(run) as consumer:
            async for event in consumer.consume_all():
                async for a2a_event in self._process_event(event):
                    yield a2a_event

    @asynccontextmanager
    async def _managed_invocation(
        self,
        run: AsyncGenerator[Event, None],
    ) -> AsyncGenerator[ADKEventConsumer, None]:
        """Set up the event queue, emitter, and agent task for one invocation.

        Yields the consumer so the caller can iterate events. Guarantees
        cleanup — queue close, emitter reset, task cancellation — on exit
        regardless of how the caller exits (normal, exception, or early break).
        """
        queue = ADKEventQueue()
        consumer = ADKEventConsumer(queue)

        def emit(event: Event) -> None:
            self._thread_events.add(event.id)
            queue.enqueue_event(event)

        # The emitter and the turn must be set BEFORE create_task so the task
        # inherits a context that holds both.
        emitter_token = set_adk_emitter(emit)
        with running_turn(self):
            agent_task = asyncio.create_task(self._run_agent(queue, run))
        agent_task.add_done_callback(consumer.agent_task_callback)

        try:
            with running_turn(self):
                yield consumer
        finally:
            if not queue.is_closed():
                queue.close()

            reset_adk_emitter(emitter_token)
            if not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task

    def started(self, invocation_context: Any) -> None:
        """Take the invocation context the Runner created for this turn.

        Called in the agent task, before the agent runs: the thread the
        agent's code talks through reads the context from there on.
        """
        self._ctx = invocation_context
        self._converter._ctx = invocation_context
        set_adk_ctx(invocation_context)

    def keeps(self, event: Event) -> bool:
        """Prepare one event for the session; False leaves it out.

        Asked for every write to the session, the Runner's and the server's
        (``TurnSessionService``). A thought, or an artifact published as it
        is, is never written (``stays_out_of_session``). An event keeps the
        author and invocation it has, and is given the turn's where it has
        none - an agent written by hand may leave them empty. An outbox is
        stored as plain data, and the last one written is the turn's.
        """
        if stays_out_of_session(event):
            return False
        if self._ctx is not None:
            event.invocation_id = event.invocation_id or self._ctx.invocation_id
            event.author = event.author or self._ctx.agent.name
        # Looked up before serializing, which drops an outbox written as None:
        # that write still clears one made earlier in the same run.
        wrote_outbox = event.actions is not None and "a2a_outbox" in event.actions.state_delta
        self._serialize_outbox(event)
        if wrote_outbox:
            self._outbox = event.actions.state_delta.get("a2a_outbox")
        return True

    async def _run_agent(
        self,
        queue: ADKEventQueue,
        run: AsyncGenerator[Event, None],
    ) -> None:
        """Drive the Runner, forwarding each event it yields.

        The Runner writes an event to the session before it yields it and
        before the agent resumes: the next model call builds its request from
        the session, and a function response missing from it would make the
        model ask for the same tool again.

        An event that reports an error is remembered and the run goes on:
        what the run does after such an event is the agent's to decide, and
        the turn's outcome is settled once the run has ended.
        """
        try:
            async with aclosing(run) as events:
                async for event in events:
                    if not event.partial:
                        self._last_agent_events.append(event)
                    if event.error_code or event.error_message:
                        self._error = ErrorResponse(code=event.error_code, message=event.error_message)
                    queue.enqueue_event(event)
        except Exception as exc:
            # Handed on through the queue and raised where the stream is read;
            # the request executor logs it there, once.
            logger.debug("Agent run failed: %s", exc)
            queue.enqueue_error(exc)
        finally:
            queue.close()

    async def _process_event(self, event: Event) -> AsyncIterator[AgentEvent]:
        """Write an event the thread emitted to the session, then convert and yield A2A events."""
        if event.id in self._thread_events:
            await self._persist_thread_event(event)

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
            yield a2a_event

    async def _persist_thread_event(self, event: Event) -> None:
        """Stamp an event the thread emitted and append it to the Runner's session.

        The Runner writes what the agent yields; an event sent through the
        thread reaches the queue directly and is written here, to the same
        session object and through the same session service, so ``keeps``
        decides for it as for any other.

        An agent's event keeps the author, branch and invocation ADK gave it:
        ADK shows a model another author's replies as quoted data, never as
        instructions, and leaves another branch's events out, so a
        sub-agent's or a remote agent's words stay theirs. An event the
        thread emits is the SDK's own and is stamped as the agent's the
        invocation was started for.
        """
        ctx = self._ctx
        event.invocation_id = ctx.invocation_id
        event.branch = ctx.branch
        event.author = ctx.agent.name
        await self._runner.session_service.append_event(session=ctx.session, event=event)

    @staticmethod
    def _serialize_outbox(event: Event) -> None:
        """Store an outbox in the state delta as plain data, which the session can serialize."""
        if event.actions and event.actions.state_delta:
            outbox = event.actions.state_delta.pop("a2a_outbox", None)
            if outbox:
                if isinstance(outbox, A2AOutbox):
                    event.actions.state_delta["a2a_outbox"] = outbox.model_dump()
                else:
                    logger.warning("Unexpected a2a_outbox type: %s", type(outbox))


__all__ = ["ADKStreamExecutor", "ADKStreamResult", "ErrorResponse"]
