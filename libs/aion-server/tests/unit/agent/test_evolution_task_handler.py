"""Tests for the toolkit-driving EvolutionTaskHandler.

The toolkit itself is optional and not installed for unit tests - the worker
factory is injected through the handler's build_worker DI seam, and the
worker/event/result DTOs are duck-typed fakes mirroring EvolutionWorker's
surface (stream/cancel). Stream-event stand-ins carry the toolkit's class
names, since the event mapper discriminates by name."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import UnsupportedOperationError

from aion.core.a2a.extensions.behaviour_evolution import (
    EvolutionDirectiveEventPayload,
    TargetContext,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
)
from aion.core.runtime import aion_a2a_extension_registry
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.core.runtime.context.models import Event
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.extensions.evolution import EvolutionTaskHandler
from aion.server.agent.execution.extensions.evolution.errors import SetupError


@pytest.fixture
def anyio_backend():
    return "asyncio"


# Stand-ins for the toolkit's typed stream events (matched by class name).
@dataclass(frozen=True)
class PhaseStarted:
    phase: SimpleNamespace
    detail: Optional[str] = None


@dataclass(frozen=True)
class BranchResolved:
    branch: str
    resumed: bool
    prior_commits: int = 0


@dataclass(frozen=True)
class ExecutorEvent:
    kind: str
    text: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpecCaptured:
    path: str
    content: str


@dataclass(frozen=True)
class RunCompleted:
    result: object


def _phase(value: str) -> PhaseStarted:
    return PhaseStarted(phase=SimpleNamespace(value=value))


def _make_context(state: TaskState = TaskState.TASK_STATE_WORKING):
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))
    message = Message(
        message_id="msg-1",
        role=Role.ROLE_USER,
        parts=[Part(text="Append a friendly sentence to README.md.")],
    )

    class _Ctx:
        current_task = task

    ctx = _Ctx()
    ctx.message = message
    return ctx


def _runtime_ctx(with_directive: bool = True, daemon=None):
    if not with_directive:
        return SimpleNamespace(
            extensions=AionRuntimeExtensions({}),
            get_daemon=lambda: daemon,
        )
    payload = EvolutionDirectiveEventPayload(
        target=TargetContext(
            repo_url="https://github.com/acme/target-agent.git",
            base_ref="HEAD",
            target_version_id="v-1",
        ),
        kind="feature",
        mode="advisory",
    )
    event = Event(
        kind=BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1,
        id="ev-1",
        source="aion://control-plane/reflection",
        payload=payload,
        raw=None,
    )
    return SimpleNamespace(
        extensions=AionRuntimeExtensions({BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1: event}),
        get_daemon=lambda: daemon,
    )


def _patch_runtime(runtime_ctx):
    return patch.object(
        AionRuntimeContextRegistry,
        "aget_current_context",
        AsyncMock(return_value=runtime_ctx),
    )


def _result(outcome: str = "succeeded", **overrides):
    values = {
        "outcome": outcome,
        "branch": "evolution/ctx-456",
        "commit_sha": "abc1234",
        "diff_summary": "1 file changed",
        "error": None,
        "resumed": False,
        "commit_count": 1,
        "pr_url": None,
        "spec_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _default_events(result) -> list:
    return [
        _phase("preparing"),
        BranchResolved(branch="evolution/ctx-456", resumed=False),
        _phase("executing"),
        ExecutorEvent(kind="agent_message", text="working on it"),
        _phase("delivering"),
        SpecCaptured(path=".aion/evolutions/ctx-456/spec.md", content="# Spec"),
        RunCompleted(result=result),
    ]


class FakeWorker:
    """Mirrors EvolutionWorker's caller-facing surface: stream/cancel."""

    def __init__(self, result, events=None):
        self._events = _default_events(result) if events is None else events
        self.cancel_called = False

    async def stream(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event

    def cancel(self):
        self.cancel_called = True


class CrashingWorker(FakeWorker):
    async def stream(self):
        await asyncio.sleep(0)
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this an async generator


def _handler(worker=None, build_worker=None):
    if build_worker is None:
        build_worker = (lambda parsed, daemon: worker) if worker is not None else None
    return EvolutionTaskHandler(build_worker=build_worker)


class TestRegistration:
    def test_uri_matches_core_constant(self):
        assert EvolutionTaskHandler.uri == BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

    def test_registers_descriptor_requiring_daemon(self):
        """Evolution is daemon-driven only — its descriptor requires the daemon
        extension to also be active. Registered centrally in aion-core registry."""
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        assert BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 in descriptors
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].requires == (DAEMON_EXTENSION_URI_V1,)

    def test_registers_descriptor_inactive_by_default(self):
        """Evolution is agent-specific, not protocol-level — inactive by default,
        requires AgentConfig.enabled_extensions to opt in."""
        aion_a2a_extension_registry.reset_to_default()
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].active is False


class TestAvailability:
    @pytest.mark.anyio
    async def test_unavailable_without_enabled_extension(self):
        handler = _handler(worker=FakeWorker(_result()))
        config = SimpleNamespace(enabled_extensions=[])

        result = await handler.availability(config)

        assert result.available is False
        assert "enabled_extensions" in result.reason

    @pytest.mark.anyio
    async def test_unavailable_without_config(self):
        result = await EvolutionTaskHandler().availability(None)
        assert result.available is False

    @pytest.mark.anyio
    async def test_available_when_enabled_and_worker_factory_injected(self):
        handler = _handler(worker=FakeWorker(_result()))
        config = SimpleNamespace(enabled_extensions=[EvolutionTaskHandler.uri])

        result = await handler.availability(config)

        assert result.available is True
        assert result.reason is None

    @pytest.mark.anyio
    async def test_unavailable_when_enabled_but_toolkit_missing(self):
        """The handler authors its own user-facing reason - it must name the
        missing toolkit, not a generic dependency guess."""
        handler = EvolutionTaskHandler()
        config = SimpleNamespace(enabled_extensions=[EvolutionTaskHandler.uri])
        with patch(
            "aion.server.agent.execution.extensions.evolution.handler._toolkit_installed",
            return_value=False,
        ):
            result = await handler.availability(config)

        assert result.available is False
        assert "behaviour-evolution toolkit" in result.reason


class TestStream:
    @pytest.mark.anyio
    async def test_maps_typed_events_and_emits_result(self):
        worker = FakeWorker(_result("succeeded"))
        handler = _handler(worker=worker)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        working = [
            e for e in out
            if isinstance(e, TaskStatusUpdateEvent)
            and e.status.state == TaskState.TASK_STATE_WORKING
        ]
        texts = [e.status.message.parts[0].text for e in working]
        assert texts == [
            "preparing",
            "started evolution branch evolution/ctx-456",
            "executing",
            "working on it",
            "delivering",
        ]

        artifacts = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)]
        names = [a.artifact.name for a in artifacts]
        assert names == ["evolution-spec", "evolution-result"]

        assert out[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert all(e.task_id == "task-123" and e.context_id == "ctx-456" for e in out)
        assert handler._running == {}

    @pytest.mark.anyio
    async def test_missing_directive_fails_task_without_running_worker(self):
        worker = FakeWorker(_result())
        handler = _handler(worker=worker)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx(with_directive=False)):
            out = [event async for event in handler.stream(ctx)]

        assert len(out) == 1
        assert out[0].status.state == TaskState.TASK_STATE_FAILED
        assert "no evolution event" in out[0].status.message.parts[0].text
        assert handler._running == {}

    @pytest.mark.anyio
    async def test_worker_factory_receives_directive_context_and_daemon_payload(self):
        """The daemon payload must reach the factory - it names the Codex
        model (environment's `llm` config var) and the principal that model
        usage is attributed to. The parsed directive must carry the task's
        A2A context id: it is the evolution's identity (branch + spec dir)."""
        daemon = SimpleNamespace(
            environment=SimpleNamespace(
                configuration_variables={"llm": "qwen"},
                daemon_agent_identity_id="daemon-1",
            )
        )
        captured = {}

        def _capture(parsed, daemon_payload):
            captured["parsed"] = parsed
            captured["daemon"] = daemon_payload
            return FakeWorker(_result())

        handler = _handler(build_worker=_capture)

        with _patch_runtime(_runtime_ctx(daemon=daemon)):
            [event async for event in handler.stream(_make_context())]

        assert captured["daemon"] is daemon
        assert captured["parsed"].payload.target.target_version_id == "v-1"
        assert captured["parsed"].context_id == "ctx-456"

    @pytest.mark.anyio
    async def test_setup_error_fails_task(self):
        def _raise(parsed, daemon):
            raise SetupError("CODEX_BASE_URL is not set")

        handler = _handler(build_worker=_raise)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        assert len(out) == 1
        assert out[0].status.state == TaskState.TASK_STATE_FAILED
        assert "CODEX_BASE_URL" in out[0].status.message.parts[0].text

    @pytest.mark.anyio
    async def test_worker_crash_fails_task(self):
        handler = _handler(worker=CrashingWorker(_result()))
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        assert out[-1].status.state == TaskState.TASK_STATE_FAILED
        assert "evolution run crashed" in out[-1].status.message.parts[0].text
        assert handler._running == {}

    @pytest.mark.anyio
    async def test_cancelled_outcome_emits_no_terminal(self):
        """The A2A cancel flow (executor's TaskUpdater.cancel) owns the
        terminal CANCELED event - the stream must not race it."""
        worker = FakeWorker(
            _result("cancelled"),
            events=[_phase("preparing"), RunCompleted(result=_result("cancelled"))],
        )
        handler = _handler(worker=worker)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        assert all(
            isinstance(e, TaskStatusUpdateEvent)
            and e.status.state == TaskState.TASK_STATE_WORKING
            for e in out
        )


class TestResume:
    @pytest.mark.anyio
    async def test_resume_re_drives_the_evolution(self):
        """Resume = run again: the toolkit finds the existing evolution branch
        and continues, so the handler simply re-streams."""
        result = _result("succeeded", resumed=True, commit_count=3)
        worker = FakeWorker(
            result,
            events=[
                _phase("preparing"),
                BranchResolved(branch="evolution/ctx-456", resumed=True, prior_commits=2),
                _phase("executing"),
                _phase("delivering"),
                RunCompleted(result=result),
            ],
        )
        handler = _handler(worker=worker)
        ctx = _make_context(state=TaskState.TASK_STATE_INPUT_REQUIRED)

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.resume(ctx)]

        texts = [
            e.status.message.parts[0].text
            for e in out
            if isinstance(e, TaskStatusUpdateEvent) and e.status.HasField("message")
        ]
        assert any("resuming evolution" in t for t in texts)
        assert out[-1].status.state == TaskState.TASK_STATE_COMPLETED


class TestCancel:
    @pytest.mark.anyio
    async def test_cancel_running_worker(self):
        handler = _handler(worker=FakeWorker(_result()))
        worker = FakeWorker(_result())
        handler._running["task-123"] = worker

        await handler.cancel(_make_context())

        assert worker.cancel_called is True

    @pytest.mark.anyio
    async def test_cancel_without_inflight_run_raises_unsupported(self):
        handler = _handler(worker=FakeWorker(_result()))
        with pytest.raises(UnsupportedOperationError):
            await handler.cancel(_make_context())
