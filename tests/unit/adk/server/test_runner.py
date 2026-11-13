"""The Runner the server builds runs on the server's services and nothing else.

ADK's ``Runner`` drives the agent, picks who answers and writes the session.
It must do so only through what the server hands it: the server's session
service and artifact service, the server's run configuration, and an
invocation context that carries the Aion runtime context. These tests hold
``build_runner`` to that, and hold ``TurnSessionService`` to passing every
call of ADK's session interface on to the server's service.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, Mock

from google.adk.agents import BaseAgent
from google.adk.agents.run_config import StreamingMode
from google.adk.events import Event
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.execution.runner import (
    RUN_CONFIG,
    AionRunner,
    TurnSessionService,
    build_runner,
    running_turn,
)
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry


def _runner(sessions=None, artifacts=None) -> AionRunner:
    return build_runner(
        BaseAgent(name="showcase"),
        app_name="agent-1",
        session_service=sessions or InMemorySessionService(),
        artifact_service=artifacts,
    )


def test_the_runner_writes_sessions_through_the_servers_service() -> None:
    sessions = InMemorySessionService()

    runner = _runner(sessions)

    assert isinstance(runner.session_service, TurnSessionService)
    assert runner.session_service.inner is sessions


def test_the_runner_keeps_artifacts_in_the_servers_service() -> None:
    artifacts = Mock()

    assert _runner(artifacts=artifacts).artifact_service is artifacts


def test_the_runner_is_given_no_service_or_plugin_of_adks_own() -> None:
    """No memory, no credentials, no plugins, no cache or compaction, no sessions created on its own."""
    runner = _runner()

    assert runner.memory_service is None
    assert runner.credential_service is None
    assert list(runner.plugin_manager.plugins) == []
    assert runner.context_cache_config is None
    assert runner.app.events_compaction_config is None
    assert runner.auto_create_session is False


def test_sessions_and_artifacts_are_keyed_by_the_aion_agent_id() -> None:
    assert _runner().app_name == "agent-1"


def test_every_invocation_is_resumable() -> None:
    assert _runner().resumability_config.is_resumable is True


def test_a_run_streams_text() -> None:
    assert RUN_CONFIG.streaming_mode == StreamingMode.SSE
    assert RUN_CONFIG.response_modalities == ["TEXT"]
    assert RUN_CONFIG.save_input_blobs_as_artifacts is False


async def test_the_invocation_context_carries_the_runtime_context(monkeypatch) -> None:
    """The model is built for real here: pydantic has to resolve every
    annotation of the ``AionRuntimeContext`` dataclass to construct it."""
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="agent-1", user_id="user")
    runtime_context = AionRuntimeContext()
    runner = _runner(sessions)

    monkeypatch.setattr(AionRuntimeContextRegistry, "get_current_context", lambda: runtime_context)
    context = runner._new_invocation_context(
        session, new_message=types.Content(parts=[types.Part(text="help")])
    )

    assert isinstance(context, AionInvocationContext)
    assert context.aion_runtime_context is runtime_context
    assert context.session is session
    assert context.session_service is runner.session_service


async def test_the_turn_in_progress_is_handed_the_invocation_context() -> None:
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="agent-1", user_id="user")
    turn = Mock()
    runner = _runner(sessions)

    with running_turn(turn):
        context = runner._new_invocation_context(
            session, new_message=types.Content(parts=[types.Part(text="help")])
        )

    turn.started.assert_called_once_with(context)


def test_the_session_service_passes_on_every_call_of_adks_interface() -> None:
    """A method ADK adds to ``BaseSessionService`` must be passed on too:
    left to the base class it would run ADK's default, not the server's service."""
    public = {
        name for name, _ in inspect.getmembers(BaseSessionService, inspect.isfunction)
        if not name.startswith("_")
    }

    passed_on = {name for name in public if name in vars(TurnSessionService)}

    assert passed_on == public


async def test_a_write_outside_a_turn_goes_to_the_servers_service_as_it_is() -> None:
    inner = Mock(append_event=AsyncMock(side_effect=lambda session, event: event))
    event = Event(author="agent")

    await TurnSessionService(inner).append_event(session="s", event=event)

    inner.append_event.assert_awaited_once_with(session="s", event=event)


async def test_a_write_the_turn_refuses_never_reaches_the_servers_service() -> None:
    inner = Mock(append_event=AsyncMock())
    turn = Mock(keeps=Mock(return_value=False))
    event = Event(author="agent")

    with running_turn(turn):
        returned = await TurnSessionService(inner).append_event(session="s", event=event)

    assert returned is event
    turn.keeps.assert_called_once_with(event)
    inner.append_event.assert_not_awaited()


def test_the_executor_builds_one_runner_per_agent_on_its_own_services() -> None:
    sessions, artifacts = InMemorySessionService(), Mock()
    executor = ADKExecutor(
        agent=BaseAgent(name="showcase"), config=Mock(), session_service=sessions, artifact_service=artifacts
    )

    runner = executor._runner("agent-1")

    assert executor._runner("agent-1") is runner
    assert runner.session_service.inner is sessions
    assert runner.artifact_service is artifacts
