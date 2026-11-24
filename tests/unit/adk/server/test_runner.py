"""The Runner the server builds runs on the server's services and nothing else.

ADK's ``Runner`` drives the agent, picks who answers and writes the session.
It must do so only through what the server hands it: the server's session
service and artifact service, the server's run configuration, the agent's
own ADK ``App`` when it was given one, and an invocation context that
carries the Aion runtime context. These tests hold ``build_runner`` to that,
hold ``TurnSessionService`` to passing every call of ADK's session interface
on to the server's service, and hold the executor to closing its Runners.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import AsyncMock, Mock

from a2a.types import Message, Part, Role
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.agents.run_config import StreamingMode
from google.adk.events import Event
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.adk.server.execution.runner import (
    APP_NAME,
    RUN_CONFIG,
    AionRunner,
    TurnSessionService,
    build_runner,
    running_turn,
)
from aion.core.config.models import AgentConfig
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.adapters import ExecutionConfig, StateScope
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider


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


def test_the_runner_is_given_no_other_service_or_plugin_of_adks_own() -> None:
    """No credentials, no plugins, no cache or compaction, no sessions created on its own."""
    runner = _runner()

    assert runner.credential_service is None
    assert list(runner.plugin_manager.plugins) == []
    assert runner.context_cache_config is None
    assert runner.app.events_compaction_config is None
    assert runner.auto_create_session is False


def test_the_runner_keeps_memory_in_the_servers_service() -> None:
    memory = Mock()

    runner = build_runner(
        BaseAgent(name="showcase"),
        app_name="agent-1",
        session_service=InMemorySessionService(),
        artifact_service=None,
        memory_service=memory,
    )

    assert runner.memory_service is memory


def test_the_executors_runner_is_given_the_servers_memory_service() -> None:
    memory = Mock()
    executor = ADKExecutor(
        BaseAgent(name="showcase"), Mock(), session_service=InMemorySessionService(), artifact_service=Mock(),
        memory_service=memory,
    )

    assert executor._runner("agent-1").memory_service is memory


def test_sessions_and_artifacts_are_keyed_by_the_aion_agent_id() -> None:
    assert _runner().app_name == "agent-1"


def test_every_invocation_is_resumable() -> None:
    assert _runner().resumability_config.is_resumable is True


def test_an_app_given_as_the_agent_runs_with_its_plugins_compaction_and_cache() -> None:
    plugin = BasePlugin(name="recorder")
    compaction = EventsCompactionConfig(compaction_interval=3, overlap_size=1)
    cache = ContextCacheConfig()
    app = App(
        name="mine",
        root_agent=BaseAgent(name="showcase"),
        plugins=[plugin],
        events_compaction_config=compaction,
        context_cache_config=cache,
    )

    runner = build_runner(app, app_name="agent-1", session_service=InMemorySessionService(), artifact_service=None)

    assert runner.agent is app.root_agent
    assert list(runner.plugin_manager.plugins) == [plugin]
    assert runner.app.events_compaction_config == compaction
    assert runner.context_cache_config == cache
    assert (runner.app_name, runner.app.name) == ("agent-1", APP_NAME)
    assert runner.resumability_config.is_resumable is True


def test_an_app_that_turns_resumability_off_runs_resumable_and_is_left_as_it_was() -> None:
    app = App(
        name="mine",
        root_agent=BaseAgent(name="showcase"),
        resumability_config=ResumabilityConfig(is_resumable=False),
    )

    runner = build_runner(app, app_name="agent-1", session_service=InMemorySessionService(), artifact_service=None)

    assert runner.resumability_config.is_resumable is True
    assert app.resumability_config.is_resumable is False
    assert app.name == "mine"


class _ClosingPlugin(BasePlugin):
    """A plugin that records being closed."""

    closed: bool = False

    async def close(self) -> None:
        self.closed = True


async def test_closing_the_executor_closes_the_plugins_of_every_runner() -> None:
    plugin = _ClosingPlugin(name="closing")
    executor = ADKExecutor(
        App(name="mine", root_agent=BaseAgent(name="showcase"), plugins=[plugin]),
        Mock(),
        session_service=InMemorySessionService(),
        artifact_service=Mock(),
        memory_service=Mock(),
    )
    executor._runner("agent-1")

    await executor.aclose()

    assert plugin.closed is True
    assert executor._runners == {}


async def test_a_runner_that_fails_to_close_leaves_the_others_to_close(caplog) -> None:
    executor = ADKExecutor(
        BaseAgent(name="showcase"), Mock(), session_service=InMemorySessionService(), artifact_service=Mock(),
        memory_service=Mock(),
    )
    failing, closing = Mock(close=AsyncMock(side_effect=RuntimeError("boom"))), Mock(close=AsyncMock())
    executor._runners = {"agent-1": failing, "agent-2": closing}

    with caplog.at_level(logging.ERROR):
        await executor.aclose()

    closing.close.assert_awaited_once()
    assert "Closing an ADK Runner failed" in caplog.text


class _EchoLlm(BaseLlm):
    """A model that answers every request, the summary of compacted events included."""

    model: str = "echo"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="echo")]), partial=False)


async def test_an_apps_events_compaction_writes_to_the_session_after_its_turns() -> None:
    """ADK compacts after an invocation's events are yielded; the turn's session writes keep the compaction."""
    sessions = InMemorySessionService()
    app = App(
        name="mine",
        root_agent=LlmAgent(name="echo", model=_EchoLlm()),
        events_compaction_config=EventsCompactionConfig(compaction_interval=2, overlap_size=0),
    )
    executor = ADKExecutor(
        app, AgentConfig(path="m:app"), session_service=sessions, artifact_service=A2AArtifactService(),
        memory_service=InMemoryMemoryService(),
    )
    scope = StateScope(agent_id="agent-1", owner_scope="owner")
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(AionRuntimeContext())
    try:
        for turn in range(2):
            request = Mock(
                task_id=f"t-{turn}",
                context_id="ctx-1",
                message=Message(message_id=f"m-{turn}", role=Role.ROLE_USER, parts=[Part(text=f"hello {turn}")]),
            )
            config = ExecutionConfig(task_id=f"t-{turn}", context_id="ctx-1", state_scope=scope)
            [event async for event in executor.stream(request, config)]
    finally:
        AionRuntimeContextRegistry.set_current_context(None)
        AionRuntimeContextRegistry.clear_provider()

    session = await sessions.get_session(app_name="agent-1", user_id="owner", session_id="ctx-1")
    assert any(event.actions and event.actions.compaction for event in session.events)


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


async def test_a_batch_goes_to_the_servers_service_in_one_call() -> None:
    inner = Mock(append_events=AsyncMock(side_effect=lambda session, events: list(events)))
    first, second = Event(author="agent"), Event(author="agent")

    returned = await TurnSessionService(inner).append_events(session="s", events=[first, second])

    inner.append_events.assert_awaited_once_with(session="s", events=[first, second])
    assert returned == [first, second]


async def test_the_turn_decides_about_each_event_of_a_batch() -> None:
    inner = Mock(append_events=AsyncMock(side_effect=lambda session, events: list(events)))
    refused, kept = Event(author="agent"), Event(author="agent")
    turn = Mock(keeps=Mock(side_effect=lambda event: event is kept))

    with running_turn(turn):
        returned = await TurnSessionService(inner).append_events(session="s", events=[refused, kept])

    inner.append_events.assert_awaited_once_with(session="s", events=[kept])
    assert returned[0] is refused and returned[1] is kept


async def test_a_batch_to_a_service_without_the_call_goes_one_event_at_a_time() -> None:
    """A session service of an ADK release before 2.15 has no ``append_events``."""
    inner = Mock(spec=["append_event"], append_event=AsyncMock(side_effect=lambda session, event: event))
    first, second = Event(author="agent"), Event(author="agent")

    returned = await TurnSessionService(inner).append_events(session="s", events=[first, second])

    assert [call.kwargs["event"] for call in inner.append_event.await_args_list] == [first, second]
    assert returned == [first, second]


async def test_flushing_one_session_is_passed_on() -> None:
    inner = Mock(flush_session=AsyncMock())

    await TurnSessionService(inner).flush_session(app_name="a", user_id="u", session_id="s")

    inner.flush_session.assert_awaited_once_with(app_name="a", user_id="u", session_id="s")


async def test_flushing_one_session_flushes_all_without_the_call() -> None:
    inner = Mock(spec=["flush"], flush=AsyncMock())

    await TurnSessionService(inner).flush_session(app_name="a", user_id="u", session_id="s")

    inner.flush.assert_awaited_once_with()


def test_the_executor_builds_one_runner_per_agent_on_its_own_services() -> None:
    sessions, artifacts = InMemorySessionService(), Mock()
    executor = ADKExecutor(
        agent=BaseAgent(name="showcase"), config=Mock(), session_service=sessions, artifact_service=artifacts
    )

    runner = executor._runner("agent-1")

    assert executor._runner("agent-1") is runner
    assert runner.session_service.inner is sessions
    assert runner.artifact_service is artifacts
