"""Tests for the toolkit-driving EvolutionTaskHandler.

The toolkit itself is optional and not installed for unit tests - the worker
factory is injected through the handler's build_worker DI seam, and the
worker/event/result DTOs are duck-typed fakes mirroring EvolutionWorker's
surface (stream/cancel). Stream-event stand-ins carry the toolkit's class
names, since the event mapper discriminates by name."""

import asyncio
import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
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
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.extensions.behaviour_evolution import (
    EVOLUTION_VIEW_ACTIVITY,
    EVOLUTION_VIEW_FULL,
    EVOLUTION_VIEW_MILESTONES,
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
from aion.server.a2a.utils import is_ephemeral_status_event
from aion.server.agent.execution.extensions.evolution import EvolutionTaskHandler
from aion.server.agent.execution.extensions.evolution import events as evolution_events
from aion.server.agent.execution.extensions.errors import ExtensionPreflightError
from aion.server.agent.execution.extensions.evolution.errors import (
    ExtensionSetupError,
    UnsupportedDirectiveError,
)
from aion.server.agent.execution.extensions.evolution import handler as handler_module
from aion.server.agent.execution.extensions.evolution.handler import _RunHandle


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
class AgentMessage:
    text: str
    final: bool = False


@dataclass(frozen=True)
class CommandCompleted:
    call_id: str
    command: str
    exit_code: Optional[int] = None
    output: Optional[str] = None
    truncated: bool = False


@dataclass(frozen=True)
class SpecCaptured:
    path: str
    content: str


@dataclass(frozen=True)
class RunCompleted:
    result: object


def _phase(value: str) -> PhaseStarted:
    return PhaseStarted(phase=SimpleNamespace(value=value))


def _make_context(
    state: TaskState = TaskState.TASK_STATE_WORKING,
    text: str = "Append a friendly sentence to README.md.",
):
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))
    parts = []
    if text:
        parts.append(Part(text=text))
    message = Message(message_id="msg-1", role=Role.ROLE_USER, parts=parts)

    class _Ctx:
        current_task = task

    ctx = _Ctx()
    ctx.message = message
    return ctx


def _payload(
    scope: str = "auto", view: str = EVOLUTION_VIEW_ACTIVITY
) -> EvolutionDirectiveEventPayload:
    return EvolutionDirectiveEventPayload(
        target=TargetContext(
            repo_url="https://github.com/acme/target-agent.git",
            base_ref="HEAD",
        ),
        kind="feature",
        mode="advisory",
        scope=scope,
        view=view,
    )


def _runtime_ctx(
    with_directive: bool = True,
    daemon=None,
    scope: str = "auto",
    view: str = EVOLUTION_VIEW_ACTIVITY,
):
    if not with_directive:
        return SimpleNamespace(
            extensions=AionRuntimeExtensions({}),
            get_daemon=lambda: daemon,
        )
    payload = _payload(scope=scope, view=view)
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
        "error": None,
        "resumed": False,
        "commit_count": 1,
        "pr_url": None,
        "spec_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _progress(event) -> dict:
    """The machine-readable progress struct on a status event."""
    return MessageToDict(event.metadata)[evolution_events.PROGRESS_METADATA_KEY]


def _default_events(result) -> list:
    return [
        _phase("preparing"),
        BranchResolved(branch="evolution/ctx-456", resumed=False),
        _phase("executing"),
        AgentMessage(text="working on it", final=False),
        _phase("delivering"),
        SpecCaptured(path=".aion/evolutions/ctx-456/spec.md", content="# Spec"),
        RunCompleted(result=result),
    ]


class FakeWorker:
    """Mirrors EvolutionWorker's caller-facing surface: stream/cancel/result.

    `result` is None until the stream drains, matching the real worker (which
    sets it as it yields the terminal RunCompleted event) — the handler reads
    it only after the stream completes."""

    def __init__(self, result, events=None):
        self._events = _default_events(result) if events is None else events
        self._terminal_result = result
        self.result = None
        self.cancel_called = False

    async def stream(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event
        self.result = self._terminal_result

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
    async def test_available_when_enabled_and_worker_factory_injected(self, monkeypatch):
        monkeypatch.setenv("CODEX_PROVIDER", "custom")
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

    @pytest.mark.anyio
    async def test_available_regardless_of_codex_provider(self, monkeypatch):
        """CODEX_PROVIDER is runtime config, not an install-time fact: some
        deployments set it onto an already-running pod after startup, and
        availability()'s result is cached for the executor's whole process
        lifetime (see request_executor.create's mark_unavailable). Caching a
        missing/unknown CODEX_PROVIDER here would wedge such a deployment as
        permanently unavailable until a restart. It is instead validated per
        request in build_worker(), which always sees the live environment."""
        monkeypatch.delenv("CODEX_PROVIDER", raising=False)
        handler = _handler(worker=FakeWorker(_result()))
        config = SimpleNamespace(enabled_extensions=[EvolutionTaskHandler.uri])

        result = await handler.availability(config)

        assert result.available is True


class TestPreflight:
    @pytest.mark.anyio
    async def test_noop_when_build_worker_injected(self):
        """The build_worker DI seam (tests) bypasses the real toolkit
        entirely, so there is no real env to check - preflight must not
        pretend otherwise."""
        handler = _handler(worker=FakeWorker(_result()))
        ctx = _make_context()

        await handler.preflight(ctx)  # must not raise

    @pytest.mark.anyio
    async def test_noop_when_toolkit_not_installed(self, monkeypatch):
        """When the optional toolkit is absent, `from .tools_factory import
        ...` fails - preflight must fail open here, the same way
        availability() already treats a missing toolkit as its own, separate
        rejection rather than raising out of an unrelated check. Forced via
        the `sys.modules[name] = None` sentinel (CPython raises
        ModuleNotFoundError for it) since the toolkit is actually installed
        in this repo's dev environment (editable install of the sibling
        aion-toolkit-behaviour-evolution-python checkout)."""
        monkeypatch.setitem(
            sys.modules,
            "aion.server.agent.execution.extensions.evolution.tools_factory",
            None,
        )
        handler = EvolutionTaskHandler()
        ctx = _make_context()

        await handler.preflight(ctx)  # must not raise

    @pytest.mark.anyio
    async def test_raises_extension_preflight_error_on_setup_error(self, monkeypatch):
        """A deployment env problem (e.g. missing GITHUB_TOKEN) must reject
        the request as ExtensionPreflightError - the executor turns that into
        a JSON-RPC error before any task is created (see request_executor).
        The toolkit isn't installed in this test env, so tools_factory is
        stood in via sys.modules rather than exercising the real one (that
        happens in test_evolution_tools_factory.py, gated on the toolkit)."""
        fake_tools_factory = ModuleType(
            "aion.server.agent.execution.extensions.evolution.tools_factory"
        )

        def _check_environment(daemon):
            raise ExtensionSetupError("GITHUB_TOKEN is not set - required to push the evolution branch")

        fake_tools_factory.check_environment = _check_environment
        monkeypatch.setitem(
            sys.modules,
            "aion.server.agent.execution.extensions.evolution.tools_factory",
            fake_tools_factory,
        )

        handler = EvolutionTaskHandler()
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx(with_directive=False)):
            with pytest.raises(ExtensionPreflightError, match="GITHUB_TOKEN"):
                await handler.preflight(ctx)


class TestEventKindDriftAtBindTime:
    """availability() is where the server and the installed toolkit are bound
    together, and the only place both halves are certain to be present - so it
    is where their event vocabularies get compared."""

    @staticmethod
    async def _availability(kinds, caplog, monkeypatch):
        monkeypatch.setenv("CODEX_PROVIDER", "custom")
        handler = _handler(worker=FakeWorker(_result()))
        config = SimpleNamespace(enabled_extensions=[EvolutionTaskHandler.uri])
        with patch(
            "aion.server.agent.execution.extensions.evolution.handler._toolkit_event_kinds",
            return_value=kinds,
        ), caplog.at_level("WARNING"):
            # Awaited inside the with-block: availability() is a coroutine, so
            # returning it unawaited would run the check after the patch and the
            # log capture had already been torn down.
            return await handler.availability(config)

    @pytest.mark.anyio
    async def test_drift_is_reported_but_the_extension_keeps_serving(self, caplog, monkeypatch):
        """Drift degrades progress; it does not break a run - the result and the
        terminal status come off worker.result, not off the stream. Refusing to
        serve would cost the caller more than the mismatch being reported."""
        result = await self._availability({"PhaseStarted", "CheckpointReached"}, caplog, monkeypatch)

        assert result.available is True
        assert "CheckpointReached" in caplog.text

    @pytest.mark.anyio
    async def test_agreement_is_silent(self, caplog, monkeypatch):
        result = await self._availability(set(evolution_events._KNOWN_EVENT_KINDS), caplog, monkeypatch)

        assert result.available is True
        assert caplog.text == ""

    @pytest.mark.anyio
    async def test_unreadable_kinds_are_not_reported_as_drift(self, caplog, monkeypatch):
        """None means 'nothing to compare against' - a toolkit that no longer
        exports the union must not read as though every kind had vanished."""
        result = await self._availability(None, caplog, monkeypatch)

        assert result.available is True
        assert caplog.text == ""


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
        # A fresh branch resolution carries no message (its facts ride the
        # progress struct), so it is present as an event but silent to the user.
        texts = [
            e.status.message.parts[0].text
            for e in working
            if e.status.HasField("message")
        ]
        assert texts == [
            evolution_events._PHASE_TEXT["preparing"],
            evolution_events._PHASE_TEXT["executing"],
            "working on it",
            evolution_events._PHASE_TEXT["delivering"],
        ]
        assert len(working) == len(texts) + 1

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
        assert (
            captured["parsed"].payload.target.repo_url
            == "https://github.com/acme/target-agent.git"
        )
        assert captured["parsed"].context_id == "ctx-456"

    @pytest.mark.anyio
    async def test_wire_stage_reaches_worker_factory_and_run_completes_without_gating(self):
        """`scope` rides the wire straight through to the worker factory - the
        handler never pauses or gates on it; the run always terminates via
        result_events (with the spec/result artifacts), never INPUT_REQUIRED."""
        captured = {}

        def _capture(parsed, daemon):
            captured["scope"] = parsed.scope
            return FakeWorker(_result("succeeded"))

        handler = _handler(build_worker=_capture)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx(scope="plan")):
            out = [event async for event in handler.stream(ctx)]

        assert captured["scope"] == "plan"
        assert out[-1].status.state == TaskState.TASK_STATE_COMPLETED
        artifacts = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)]
        assert [a.artifact.name for a in artifacts] == ["evolution-spec", "evolution-result"]
        assert all(
            not (
                isinstance(e, TaskStatusUpdateEvent)
                and e.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            )
            for e in out
        )

    @pytest.mark.anyio
    async def test_implement_stage_reaches_worker_factory(self):
        captured = {}

        def _capture(parsed, daemon):
            captured["scope"] = parsed.scope
            return FakeWorker(_result("succeeded"))

        handler = _handler(build_worker=_capture)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx(scope="implement")):
            [event async for event in handler.stream(ctx)]

        assert captured["scope"] == "implement"

    @pytest.mark.anyio
    async def test_setup_error_carries_the_reason_and_a_stable_code(self):
        """The caller is this deployment's own trusted control plane, so a
        setup failure names the reason (env var, missing value) rather than
        hiding it behind a generic string — the code stays stable for
        callers that branch on it programmatically."""

        def _raise(parsed, daemon):
            raise ExtensionSetupError("CODEX_PROVIDER is not set")

        handler = _handler(build_worker=_raise)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        assert len(out) == 1
        assert out[0].status.state == TaskState.TASK_STATE_FAILED
        assert "CODEX_PROVIDER" in out[0].status.message.parts[0].text
        assert _progress(out[0])["error"]["code"] == "misconfigured_deployment"

    @pytest.mark.anyio
    async def test_unsupported_directive_keeps_its_detail(self):
        """The mirror case: only the caller can route around a directive this
        deployment cannot serve, so the detail has to reach them."""

        def _raise(parsed, daemon):
            raise UnsupportedDirectiveError("mode='directive' (installed toolkit accepts: 'advisory')")

        handler = _handler(build_worker=_raise)
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        text = out[0].status.message.parts[0].text
        assert "mode='directive'" in text
        assert _progress(out[0])["error"]["code"] == "unsupported_directive"

    @pytest.mark.anyio
    async def test_worker_crash_fails_task(self):
        handler = _handler(worker=CrashingWorker(_result()))
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        assert out[-1].status.state == TaskState.TASK_STATE_FAILED
        # A wiring bug's text is a traceback fragment; the caller gets a code.
        assert "boom" not in out[-1].status.message.parts[0].text
        assert _progress(out[-1])["error"]["code"] == "internal_error"
        assert handler._running == {}

    @pytest.mark.anyio
    async def test_worker_crash_text_says_where_the_run_had_got_to(self):
        """The traceback cannot be shown, but the run's own progress can: a
        crash after the executor started means work may have been done, and
        the requester is told that instead of a bare "internal error"."""

        class CrashMidRun(FakeWorker):
            async def stream(self):
                yield _phase("executing")
                raise RuntimeError("boom")

        handler = _handler(worker=CrashMidRun(_result()))
        ctx = _make_context()

        with _patch_runtime(_runtime_ctx()):
            out = [event async for event in handler.stream(ctx)]

        text = out[-1].status.message.parts[0].text
        assert text == (
            "Failed — The improver hit an internal error while making the change. "
            "Any work in progress was lost; no changes were delivered. "
            "The details are in the deployment's logs."
        )
        assert _progress(out[-1])["stage"] == "executing"

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


class TestStreamView:
    """The directive's `view` decides how much of the run reaches the caller.

    `milestones` is defined by the ephemeral mark rather than by a list of
    event kinds, so what it delivers cannot drift from what the task record
    keeps."""

    def _events(self, result):
        return [
            _phase("preparing"),
            BranchResolved(branch="evolution/ctx-456", resumed=False),
            _phase("executing"),
            CommandCompleted(call_id="c", command="cat .env", exit_code=0, output="TOKEN=abc"),
            AgentMessage(text="working on it", final=False),
            AgentMessage(text="Added retries", final=True),
            SpecCaptured(path=".aion/evolutions/ctx-456/spec.md", content="# Spec"),
            RunCompleted(result=result),
        ]

    async def _run(self, view: str):
        result = _result("succeeded")
        handler = _handler(worker=FakeWorker(result, events=self._events(result)))
        with _patch_runtime(_runtime_ctx(view=view)):
            return [event async for event in handler.stream(_make_context())]

    @staticmethod
    def _command_payloads(out):
        payloads = []
        for event in out:
            if not (isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")):
                continue
            for part in event.status.message.parts:
                if not part.HasField("data"):
                    continue
                data = MessageToDict(part.data)
                if "command" in data:
                    payloads.append(data)
        return payloads

    @pytest.mark.anyio
    async def test_full_view_streams_command_output(self):
        assert self._command_payloads(await self._run(EVOLUTION_VIEW_FULL))[0]["output"] == "TOKEN=abc"

    @pytest.mark.anyio
    async def test_activity_view_keeps_the_command_and_drops_its_output(self):
        payload = self._command_payloads(await self._run(EVOLUTION_VIEW_ACTIVITY))[0]

        assert payload["command"] == "cat .env"
        assert payload["exitCode"] == 0
        assert "output" not in payload

    @pytest.mark.anyio
    async def test_milestones_view_drops_every_ephemeral_event(self):
        out = await self._run(EVOLUTION_VIEW_MILESTONES)

        assert self._command_payloads(out) == []
        texts = [
            e.status.message.parts[0].text
            for e in out
            if isinstance(e, TaskStatusUpdateEvent) and e.status.HasField("message")
        ]
        # Phase narration and the intermediate message are gone; the run's one
        # durable message and the terminal summary remain.
        assert "working on it" not in texts
        assert "Added retries" in texts
        assert not any(t in texts for t in evolution_events._PHASE_TEXT.values())

    @pytest.mark.anyio
    async def test_milestones_view_still_delivers_artifacts_and_terminal_state(self):
        """A reduced view bounds the chronicle, not the result: whoever asked
        for `milestones` still needs to know what the run produced."""
        out = await self._run(EVOLUTION_VIEW_MILESTONES)

        artifacts = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)]
        assert [a.artifact.name for a in artifacts] == ["evolution-spec", "evolution-result"]
        assert out[-1].status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.anyio
    async def test_milestones_view_delivers_exactly_the_durable_events(self):
        """The invariant that justifies defining the view by the mark: nothing
        the caller receives is an event the task manager would have skipped."""
        out = await self._run(EVOLUTION_VIEW_MILESTONES)

        assert not any(is_ephemeral_status_event(e) for e in out)


class TestResume:
    @pytest.mark.anyio
    async def test_resume_re_drives_the_evolution(self):
        """Resume delegates straight to stream(): the toolkit finds the
        existing evolution branch and continues, so the handler simply
        re-streams. The interrupted state that made this task resumable is
        produced outside the handler (which never emits INPUT_REQUIRED
        itself)."""
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
        assert any("Picking up where the previous run left off" in t for t in texts)
        assert out[-1].status.state == TaskState.TASK_STATE_COMPLETED


class _FakeEventQueue:
    """Collects whatever `cancel()` enqueues, standing in for the real
    a2a-sdk `EventQueue` the executor hands the handler."""

    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


def _inflight(handler, result=None, *, finished=True) -> FakeWorker:
    """Register a run as in flight on `handler`, as _drive would.

    `finished` mirrors whether the run has already unwound: cancel() waits on
    that rendezvous before it can report anything.
    """
    worker = FakeWorker(_result())
    handle = _RunHandle(worker)
    handle.result = result
    if finished:
        handle.finished.set()
    handler._running["task-123"] = handle
    return worker


class TestCancel:
    @pytest.mark.anyio
    async def test_cancel_running_worker(self):
        handler = _handler(worker=FakeWorker(_result()))
        worker = _inflight(handler, _result("cancelled"))

        await handler.cancel(_make_context(), _FakeEventQueue())

        assert worker.cancel_called is True

    @pytest.mark.anyio
    async def test_cancel_reports_rescued_work_on_the_terminal_message(self):
        """A cancelled run's rescue outcome has nowhere else to go: events
        published after the terminal CANCELED are dropped by the consumer, so
        it rides the CANCELED itself."""
        handler = _handler(worker=FakeWorker(_result()))
        _inflight(
            handler,
            _result("cancelled", rescue_pushed=True, branch="evolution/ctx-456"),
        )

        message = await handler.cancel(_make_context(), _FakeEventQueue())

        assert isinstance(message, Message)
        assert "evolution/ctx-456" in message.parts[0].text
        payload = MessageToDict(message.parts[1].data)
        assert payload["outcome"] == "cancelled"
        assert payload["rescuePushed"] is True

    @pytest.mark.anyio
    async def test_cancel_publishes_the_rescue_bundle_as_an_artifact(self):
        """Unified with the failed/completed path: the bundle rides on
        `event_queue` as its own artifact, published before the terminal
        message is returned — not as bytes riding the message itself."""
        handler = _handler(worker=FakeWorker(_result()))
        _inflight(
            handler,
            _result(
                "cancelled",
                rescue_pushed=False,
                branch=None,
                rescue_bundle_created=True,
                rescue_bundle=b"PACK-git-bundle-bytes",
            ),
        )
        queue = _FakeEventQueue()

        message = await handler.cancel(_make_context(), queue)

        assert len(queue.events) == 1
        bundle_event = queue.events[0]
        assert bundle_event.artifact.name == evolution_events.RESCUE_BUNDLE_ARTIFACT_NAME
        assert bundle_event.artifact.parts[0].raw == b"PACK-git-bundle-bytes"
        # No raw bytes on the terminal message itself — the artifact already
        # carries them.
        assert not any(hasattr(p, "raw") and p.raw for p in message.parts)

    @pytest.mark.anyio
    async def test_cancel_says_nothing_when_the_run_already_finished(self):
        """The run reached its own outcome inside the race window and _drive
        already reported it; a second report here would contradict it."""
        handler = _handler(worker=FakeWorker(_result()))
        _inflight(handler, _result("succeeded"))

        assert await handler.cancel(_make_context(), _FakeEventQueue()) is None

    @pytest.mark.anyio
    async def test_cancel_gives_up_reporting_rather_than_hanging(self, monkeypatch):
        """The wait for the unwind is bounded: a run that will not finish must
        not hold the cancel request open. The CANCELED still goes out, bare."""
        monkeypatch.setattr(handler_module, "_CANCEL_DRAIN_TIMEOUT_S", 0.01)
        handler = _handler(worker=FakeWorker(_result()))
        worker = _inflight(handler, _result("cancelled"), finished=False)

        assert await handler.cancel(_make_context(), _FakeEventQueue()) is None
        assert worker.cancel_called is True

    @pytest.mark.anyio
    async def test_cancel_without_inflight_run_raises_unsupported(self):
        handler = _handler(worker=FakeWorker(_result()))
        with pytest.raises(UnsupportedOperationError):
            await handler.cancel(_make_context(), _FakeEventQueue())
