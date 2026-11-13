"""ADK's own ``Runner``, set up so that the server decides what is stored and where.

The server runs an agent the way ADK does: through ``Runner.run_async``.
The Runner picks the agent that answers a message, resumes a paused
invocation, writes the user's message and the agent's events to the session
and drives the agent. Nothing of that is repeated here.

What stays the server's is everything the Runner is given:

- the session service and the artifact service are the server's own, and
  no memory service, credential service or plugin is passed;
- every write to the session goes through ``TurnSessionService``, where the
  turn in progress decides whether an event is kept and prepares it, and
  where a file the event carries inline is stored and kept as a link
  (``session_files``);
- the invocation context is ``AionInvocationContext``, carrying the Aion
  runtime context of the request;
- the run configuration and resumability are fixed by ``build_runner`` and
  ``RUN_CONFIG``, not left to ADK's defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional, Protocol

from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.server.execution.session_files import kept_in_session
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from google.adk.agents import BaseAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App, ResumabilityConfig
from google.adk.artifacts import BaseArtifactService
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, Session

__all__ = [
    "APP_NAME",
    "RUN_CONFIG",
    "AionRunner",
    "Turn",
    "TurnSessionService",
    "build_runner",
    "running_turn",
]

APP_NAME = "aion_agent"
"""The name of the ADK ``App`` the agent is wrapped in. ADK requires an
identifier there; sessions and artifacts are keyed by the Runner's
``app_name``, which is the Aion agent id."""

RUN_CONFIG = RunConfig(
    response_modalities=["TEXT"],
    streaming_mode=StreamingMode.SSE,
)
"""How every invocation runs: text out, streamed as the model produces it."""


class Turn(Protocol):
    """One turn in progress, as the Runner's services see it."""

    def started(self, invocation_context: AionInvocationContext) -> None:
        """Take the invocation context the Runner created for this turn."""

    def keeps(self, event: Event) -> bool:
        """Prepare ``event`` for the session; False leaves it out of the session."""


_TURN: ContextVar[Optional[Turn]] = ContextVar("aion_adk_turn", default=None)


@contextmanager
def running_turn(turn: Turn) -> Iterator[None]:
    """Make ``turn`` the one the Runner's services report to in this context.

    Entered before the task that drives the Runner is created, so that the
    task and the code reading its events share the turn.
    """
    token = _TURN.set(turn)
    try:
        yield
    finally:
        _TURN.reset(token)


class TurnSessionService(BaseSessionService):
    """The server's session service as the Runner sees it.

    Every call goes to the wrapped service unchanged, except
    ``append_event``: the turn in progress is asked first (``Turn.keeps``),
    so one place decides what a session holds, whoever writes - the Runner,
    for the user's message and the agent's events, or the server, for what
    the agent sent through the thread. The session is given a copy of the
    event with its inline files stored and linked (``kept_in_session``); the
    event itself goes back to the writer as it was.
    """

    def __init__(self, inner: BaseSessionService) -> None:
        self._inner = inner

    @property
    def inner(self) -> BaseSessionService:
        """The session service every call is passed on to."""
        return self._inner

    async def create_session(self, **kwargs: Any) -> Session:
        return await self._inner.create_session(**kwargs)

    async def get_session(self, **kwargs: Any) -> Optional[Session]:
        return await self._inner.get_session(**kwargs)

    async def list_sessions(self, **kwargs: Any) -> Any:
        return await self._inner.list_sessions(**kwargs)

    async def delete_session(self, **kwargs: Any) -> None:
        await self._inner.delete_session(**kwargs)

    async def get_user_state(self, **kwargs: Any) -> Any:
        return await self._inner.get_user_state(**kwargs)

    async def flush(self) -> None:
        flush = getattr(self._inner, "flush", None)
        if flush is not None:
            await flush()

    async def append_event(self, session: Session, event: Event) -> Event:
        turn = _TURN.get()
        if turn is not None and not turn.keeps(event):
            return event
        kept = await kept_in_session(event)
        appended = await self._inner.append_event(session=session, event=kept)
        return event if kept is not event else appended


class AionRunner(Runner):
    """ADK's ``Runner`` whose invocations carry the Aion runtime context."""

    def _create_invocation_context(self, **kwargs: Any) -> AionInvocationContext:
        invocation_context = AionInvocationContext(
            **kwargs,
            aion_runtime_context=AionRuntimeContextRegistry.get_current_context(),
        )
        turn = _TURN.get()
        if turn is not None:
            turn.started(invocation_context)
        return invocation_context

    def _enforce_app_name_alignment(self) -> None:
        """Skip ADK's check that the app name matches the agent's directory.

        The app name here is the Aion agent id by design; ADK's warning
        about a mismatch would send the reader looking for a fault.
        """
        self._app_name_alignment_hint = None

    def _warn_uncached_agent_transfer(self) -> None:
        """Skip ADK's advice to set a context cache for agents that transfer.

        The server passes no context cache configuration, so the advice
        cannot be followed here.
        """


def build_runner(
        agent: BaseAgent,
        *,
        app_name: str,
        session_service: BaseSessionService,
        artifact_service: Optional[BaseArtifactService],
) -> AionRunner:
    """Build the Runner that runs ``agent`` for the Aion agent ``app_name``.

    Every invocation is resumable, as in an ADK ``App`` with
    ``ResumabilityConfig(is_resumable=True)``: a run that makes a
    long-running tool call - ``request_input``, a tool confirmation, a
    ``LongRunningFunctionTool`` - stops there, and the reply resumes the same
    invocation. Without it ADK calls the model again at once and the user is
    never asked.

    Args:
        agent: The root agent.
        app_name: The Aion agent id; sessions and artifacts are keyed by it.
        session_service: The server's session service. The Runner writes
            through ``TurnSessionService`` around it.
        artifact_service: The server's artifact service.
    """
    app = App(
        name=APP_NAME,
        root_agent=agent,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    return AionRunner(
        app=app,
        app_name=app_name,
        session_service=TurnSessionService(session_service),
        artifact_service=artifact_service,
    )
