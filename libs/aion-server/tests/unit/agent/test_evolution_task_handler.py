"""Tests for the toolkit-driving EvolutionTaskHandler.

The toolkit itself is optional and not installed for unit tests - the worker
factory is injected through the handler's build_worker DI seam, and the
worker/result DTOs are duck-typed fakes mirroring EvolutionWorker's surface
(run/snapshot/cancel)."""

import asyncio
from types import SimpleNamespace
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
        "branch": "evolution/v-1-1",
        "commit_sha": "abc1234",
        "diff_summary": "1 file changed",
        "error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeWorker:
    """Mirrors EvolutionWorker's caller-facing surface: run/snapshot/cancel."""

    def __init__(self, result, phases=("cloning", "applying", "pushing")):
        self._snapshots = [
            SimpleNamespace(phase=SimpleNamespace(value=phase), detail=None)
            for phase in phases
        ]
        self._result = result
        self._index = 0
        self.cancel_called = False

    def snapshot(self):
        return self._snapshots[self._index]

    async def run(self):
        for index in range(len(self._snapshots)):
            self._index = index
            await asyncio.sleep(0)
        return self._result

    def cancel(self):
        self.cancel_called = True


class CrashingWorker(FakeWorker):
    async def run(self):
        await asyncio.sleep(0)
        raise RuntimeError("boom")


def _handler(worker=None, build_worker=None):
    if build_worker is None:
        build_worker = (lambda parsed, daemon: worker) if worker is not None else None
    return EvolutionTaskHandler(build_worker=build_worker, poll_interval_s=0.005)


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
    async def test_drives_worker_and_emits_progress_artifact_terminal(self):
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
        assert working, "expected at least one WORKING phase event"
        assert working[0].status.message.parts[0].text == "cloning"

        artifacts = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 1

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
    async def test_worker_factory_receives_directive_and_daemon_payload(self):
        """The daemon payload must reach the factory - it names the Codex
        model (environment's `llm` config var) and the principal that model
        usage is attributed to."""
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
        handler = _handler(worker=FakeWorker(_result("cancelled")))
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
    async def test_resume_fails_explicitly(self):
        handler = _handler(worker=FakeWorker(_result()))
        ctx = _make_context(state=TaskState.TASK_STATE_INPUT_REQUIRED)

        out = [event async for event in handler.resume(ctx)]

        assert len(out) == 1
        assert out[0].status.state == TaskState.TASK_STATE_FAILED
        assert "single-shot" in out[0].status.message.parts[0].text


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
