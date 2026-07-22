"""Tests for the toolkit-event -> A2A event mappers (toolkit-free, duck-typed).

The toolkit is not installed for unit tests: stream events are stand-in
dataclasses whose *class names* match the toolkit's event types, since the
mapper discriminates by name and reads fields duck-typed.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import pytest
from google.protobuf.json_format import MessageToDict

from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.server.agent.execution.extensions.evolution import events


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


def _task() -> Task:
    return Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def _result(outcome: str = "succeeded", **overrides):
    values = {
        "outcome": outcome,
        "branch": "evolution/ctx-1",
        "commit_sha": "abc1234",
        "diff_summary": "1 file changed",
        "error": None,
        "resumed": False,
        "commit_count": 3,
        "pr_url": None,
        "spec_path": ".aion/evolutions/ctx-1/retries.md",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _text(event: TaskStatusUpdateEvent) -> str:
    return event.status.message.parts[0].text


def _progress(event: TaskStatusUpdateEvent) -> dict:
    return MessageToDict(event.metadata)[events.PROGRESS_METADATA_KEY]


class TestMapStreamEvent:
    def test_phase_started_carries_stage_metadata(self):
        event = events.map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="executing")))

        assert isinstance(event, TaskStatusUpdateEvent)
        assert event.status.state == TaskState.TASK_STATE_WORKING
        assert _text(event) == "executing"
        assert _progress(event) == {"stage": "executing"}

    def test_branch_resolved_fresh(self):
        event = events.map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=False)
        )

        assert "started evolution branch evolution/ctx-1" in _text(event)
        assert _progress(event) == {
            "stage": "branch",
            "branch": "evolution/ctx-1",
            "resumed": False,
            "priorCommits": 0,
        }

    def test_branch_resolved_resumed_names_prior_work(self):
        event = events.map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=True, prior_commits=4)
        )

        assert "resuming evolution" in _text(event)
        assert "4 commit(s)" in _text(event)
        assert _progress(event)["resumed"] is True
        assert _progress(event)["priorCommits"] == 4

    def test_agent_message_surfaced_with_executor_metadata(self):
        event = events.map_stream_event(
            _task(), ExecutorEvent(kind="agent_message", text="Implemented retries")
        )

        assert _text(event) == "Implemented retries"
        assert _progress(event) == {"stage": "executing", "executorKind": "agent_message"}

    def test_command_execution_surfaced_as_shell_line(self):
        event = events.map_stream_event(
            _task(), ExecutorEvent(kind="command_execution", text="pytest -q")
        )
        assert _text(event) == "$ pytest -q"

    def test_unsurfaced_executor_kinds_are_dropped(self):
        assert events.map_stream_event(_task(), ExecutorEvent(kind="reasoning", text="hmm")) is None
        assert events.map_stream_event(_task(), ExecutorEvent(kind="turn.completed")) is None
        assert (
            events.map_stream_event(_task(), ExecutorEvent(kind="agent_message", text=None)) is None
        )

    def test_spec_captured_becomes_markdown_artifact(self):
        event = events.map_stream_event(
            _task(), SpecCaptured(path=".aion/evolutions/ctx-1/retries.md", content="# Spec")
        )

        assert isinstance(event, TaskArtifactUpdateEvent)
        assert event.artifact.name == events.SPEC_ARTIFACT_NAME
        assert event.last_chunk is True
        assert event.artifact.parts[0].text == "# Spec"
        meta = MessageToDict(event.artifact.metadata)[events.PROGRESS_METADATA_KEY]
        assert meta == {"path": ".aion/evolutions/ctx-1/retries.md"}

    def test_unknown_event_types_are_dropped(self):
        assert events.map_stream_event(_task(), SimpleNamespace()) is None


class TestEventKindDriftGuard:
    def test_known_event_kinds_cover_every_toolkit_event_type(self):
        """`events._KNOWN_EVENT_KINDS` must name every class the toolkit's
        `EvolutionEvent` union can carry, so a toolkit-side rename or addition
        is caught here — as a loud test failure — instead of only a
        `logger.warning` line the first time it happens in production."""
        toolkit = pytest.importorskip("aion.toolkits.behaviour_evolution")
        from typing import get_args

        event_types = get_args(toolkit.EvolutionEvent)
        assert event_types, "EvolutionEvent resolved to no union members"
        names = {t.__name__ for t in event_types}
        assert names == events._KNOWN_EVENT_KINDS


class TestFailedEvent:
    def test_failed_state_and_message(self):
        event = events.failed_event(_task(), error="CODEX_BASE_URL is not set")
        assert event.status.state == TaskState.TASK_STATE_FAILED
        assert _text(event) == "CODEX_BASE_URL is not set"


class TestResultEvents:
    def test_succeeded_emits_schema_tagged_artifact_then_completed(self):
        out = events.result_events(_task(), _result("succeeded"))

        assert len(out) == 2
        artifact_event, terminal = out
        assert isinstance(artifact_event, TaskArtifactUpdateEvent)
        assert artifact_event.artifact.name == events.RESULT_ARTIFACT_NAME
        assert artifact_event.last_chunk is True

        part = artifact_event.artifact.parts[0]
        data = MessageToDict(part.data)
        assert data["outcome"] == "succeeded"
        assert data["branch"] == "evolution/ctx-1"
        assert data["commitSha"] == "abc1234"
        assert data["commitCount"] == 3
        assert data["specPath"] == ".aion/evolutions/ctx-1/retries.md"
        assert "error" not in data

        part_meta = MessageToDict(part.metadata)
        assert part_meta[EVENT_EXTENSION_URI_V1]["schema"] == (
            BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1
        )

        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        assert "evolution/ctx-1" in _text(terminal)
        assert "abc1234" in _text(terminal)

    def test_resumed_run_reports_resumed_flag(self):
        out = events.result_events(_task(), _result("succeeded", resumed=True))
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["resumed"] is True

    def test_pr_url_lands_in_payload_and_terminal_text(self):
        out = events.result_events(
            _task(), _result("succeeded", pr_url="https://github.com/acme/x/pull/7")
        )
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["prUrl"] == "https://github.com/acme/x/pull/7"
        assert "pull/7" in _text(out[1])

    def test_no_change_completes_with_explanation(self):
        out = events.result_events(
            _task(),
            _result(
                "no_change", branch=None, commit_sha=None, diff_summary=None, commit_count=0,
                spec_path=None,
            ),
        )

        artifact_event, terminal = out
        assert MessageToDict(artifact_event.artifact.parts[0].data)["outcome"] == "no_change"
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        assert _text(terminal) == "no changes produced"

    def test_failed_reports_error_in_artifact_and_status(self):
        out = events.result_events(
            _task(),
            _result(
                "failed",
                branch=None,
                commit_sha=None,
                diff_summary=None,
                error="push denied",
                commit_count=None,
                spec_path=None,
            ),
        )

        artifact_event, terminal = out
        assert MessageToDict(artifact_event.artifact.parts[0].data)["error"] == "push denied"
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert _text(terminal) == "push denied"

    def test_cancelled_emits_nothing(self):
        """The A2A cancel flow owns the terminal CANCELED event; a cancelled
        run must not race it with its own terminal status or artifact."""
        assert events.result_events(_task(), _result("cancelled")) == []
