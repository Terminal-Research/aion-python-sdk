"""Tests for the toolkit-DTO -> A2A event mappers (toolkit-free, duck-typed)."""

from types import SimpleNamespace

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


def _task() -> Task:
    return Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def _snapshot(phase: str = "cloning", detail: str | None = None):
    return SimpleNamespace(phase=SimpleNamespace(value=phase), detail=detail)


def _result(outcome: str = "succeeded", **overrides):
    values = {
        "outcome": outcome,
        "branch": "evolution/v-1-1752000000",
        "commit_sha": "abc1234",
        "diff_summary": "1 file changed",
        "error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _text(event: TaskStatusUpdateEvent) -> str:
    return event.status.message.parts[0].text


class TestSnapshotEvent:
    def test_phase_only(self):
        event = events.snapshot_event(_task(), _snapshot("applying"))

        assert isinstance(event, TaskStatusUpdateEvent)
        assert event.task_id == "task-1"
        assert event.context_id == "ctx-1"
        assert event.status.state == TaskState.TASK_STATE_WORKING
        assert _text(event) == "applying"

    def test_phase_with_detail(self):
        event = events.snapshot_event(_task(), _snapshot("applying", detail="attempt 2"))
        assert _text(event) == "applying: attempt 2"


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
        assert data["branch"] == "evolution/v-1-1752000000"
        assert data["commitSha"] == "abc1234"
        assert "error" not in data

        part_meta = MessageToDict(part.metadata)
        assert part_meta[EVENT_EXTENSION_URI_V1]["schema"] == (
            BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1
        )

        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        assert "evolution/v-1-1752000000" in _text(terminal)
        assert "abc1234" in _text(terminal)

    def test_no_change_completes_with_explanation(self):
        out = events.result_events(
            _task(), _result("no_change", branch=None, commit_sha=None, diff_summary=None)
        )

        artifact_event, terminal = out
        assert MessageToDict(artifact_event.artifact.parts[0].data)["outcome"] == "no_change"
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        assert _text(terminal) == "no changes produced"

    def test_failed_reports_error_in_artifact_and_status(self):
        out = events.result_events(
            _task(),
            _result("failed", branch=None, commit_sha=None, diff_summary=None, error="push denied"),
        )

        artifact_event, terminal = out
        assert MessageToDict(artifact_event.artifact.parts[0].data)["error"] == "push denied"
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert _text(terminal) == "push denied"

    def test_cancelled_emits_nothing(self):
        """The A2A cancel flow owns the terminal CANCELED event; a cancelled
        run must not race it with its own terminal status or artifact."""
        assert events.result_events(_task(), _result("cancelled")) == []
