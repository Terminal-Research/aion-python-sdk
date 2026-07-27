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
from aion.core.a2a.extensions.behaviour_evolution import (
    EVOLUTION_VIEW_ACTIVITY,
    EVOLUTION_VIEW_FULL,
    EVOLUTION_VIEW_MILESTONES,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.server.a2a.utils import is_ephemeral_status_event
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
class CommandStarted:
    call_id: str
    command: str


@dataclass(frozen=True)
class CommandCompleted:
    call_id: str
    command: str
    exit_code: Optional[int] = None
    output: Optional[str] = None
    truncated: bool = False


@dataclass(frozen=True)
class AgentMessage:
    text: str
    final: bool = False


@dataclass(frozen=True)
class ExecutorTrace:
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


def _data(event: TaskStatusUpdateEvent) -> dict:
    """The typed payload carried on the status message's data part."""
    return MessageToDict(event.status.message.parts[1].data)


def _part_schema(event: TaskStatusUpdateEvent) -> str:
    """The schema URI tagged on the status message's data part."""
    return MessageToDict(event.status.message.parts[1].metadata)[EVENT_EXTENSION_URI_V1]["schema"]


class TestMapStreamEvent:
    def test_phase_started_carries_stage_metadata(self):
        # The wrapper owns phrasing: the phase enum maps to a human sentence,
        # while the raw token stays on the progress `stage` for machines.
        event = events.map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="executing")))

        assert isinstance(event, TaskStatusUpdateEvent)
        assert event.status.state == TaskState.TASK_STATE_WORKING
        assert _text(event) == events._PHASE_TEXT["executing"]
        assert _text(event) != "executing"
        assert _progress(event) == {"stage": "executing"}
        # Narration of a phase the run has already left: streamed live, kept
        # out of the durable record. What happened in the phase survives on the
        # result artifact and the final summary.
        assert is_ephemeral_status_event(event) is True

    def test_unmapped_phase_falls_back_to_raw_token(self):
        event = events.map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="brand-new")))
        assert _text(event) == "brand-new"
        assert _progress(event) == {"stage": "brand-new"}

    def test_branch_resolved_fresh(self):
        event = events.map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=False)
        )

        # A fresh start is the default, and the branch name is plumbing: no
        # message at all. The structured facts still ride the event.
        assert not event.status.HasField("message")
        # BranchResolved is a fact within the PREPARING phase, not its own phase —
        # stage matches the toolkit's Phase.PREPARING, same as a bare PhaseStarted.
        assert _progress(event) == {
            "stage": "preparing",
            "branch": "evolution/ctx-1",
            "resumed": False,
            "priorCommits": 0,
        }
        # A persisted milestone, not live progress: it is the only record of
        # which branch a run that later fails was working on.
        assert is_ephemeral_status_event(event) is False

    def test_branch_resolved_resumed_names_prior_work(self):
        event = events.map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=True, prior_commits=4)
        )

        # The one fact the user cannot infer, in their terms — no branch name,
        # no git vocabulary.
        assert "Picking up where the previous run left off" in _text(event)
        assert "4 change(s)" in _text(event)
        assert "evolution/ctx-1" not in _text(event)
        assert _progress(event)["resumed"] is True
        assert _progress(event)["priorCommits"] == 4

    def test_resume_without_prior_work_omits_the_count(self):
        event = events.map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=True, prior_commits=0)
        )
        assert _text(event) == "Picking up where the previous run left off"

    def test_command_started_is_ephemeral_typed_and_correlated(self):
        event = events.map_stream_event(
            _task(), CommandStarted(call_id="call_1", command="pytest -q")
        )

        assert _text(event) == "running $ pytest -q"
        assert is_ephemeral_status_event(event) is True
        assert _part_schema(event) == BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1
        assert _data(event) == {"callId": "call_1", "command": "pytest -q"}
        assert _progress(event) == {
            "stage": "executing",
            "executorKind": "command_execution",
            "callId": "call_1",
        }

    def test_command_completed_carries_exit_code_and_output(self):
        event = events.map_stream_event(
            _task(),
            CommandCompleted(
                call_id="call_1", command="pytest -q", exit_code=0, output="3 passed"
            ),
            view=EVOLUTION_VIEW_FULL,
        )

        assert _text(event) == "$ pytest -q"
        assert is_ephemeral_status_event(event) is True
        assert _part_schema(event) == BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1
        assert _data(event) == {
            "callId": "call_1",
            "command": "pytest -q",
            "exitCode": 0,
            "output": "3 passed",
            "truncated": False,
        }
        # The output rides the typed payload only — the progress struct must
        # not carry a second copy of it on every command the executor runs.
        assert _progress(event) == {
            "stage": "executing",
            "executorKind": "command_execution",
            "callId": "call_1",
            "exitCode": 0,
        }

    def test_command_completed_nonzero_exit_is_flagged_in_text(self):
        event = events.map_stream_event(
            _task(), CommandCompleted(call_id="c", command="pytest -q", exit_code=1)
        )
        assert _text(event) == "$ pytest -q (exit 1)"
        assert _progress(event)["exitCode"] == 1

    def test_command_completed_bounds_chatty_output_and_flags_truncated(self):
        big = "x" * (events._COMMAND_OUTPUT_TAIL_CHARS + 500)
        event = events.map_stream_event(
            _task(),
            CommandCompleted(call_id="c", command="cat big.log", output=big),
            view=EVOLUTION_VIEW_FULL,
        )
        data = _data(event)
        assert len(data["output"]) == events._COMMAND_OUTPUT_TAIL_CHARS
        assert data["output"] == big[-events._COMMAND_OUTPUT_TAIL_CHARS:]
        assert data["truncated"] is True

    def test_command_completed_without_exit_code_omits_it(self):
        event = events.map_stream_event(
            _task(), CommandCompleted(call_id="c", command="ls")
        )
        assert _text(event) == "$ ls"
        assert "exitCode" not in _progress(event)
        assert "exitCode" not in _data(event)

    def test_intermediate_agent_message_is_ephemeral(self):
        event = events.map_stream_event(_task(), AgentMessage(text="working", final=False))

        assert _text(event) == "working"
        assert is_ephemeral_status_event(event) is True
        assert _part_schema(event) == BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1
        # Both representations ride the event on purpose: prose for the user,
        # a typed payload for a consumer that does not parse prose.
        assert _data(event) == {"text": "working", "final": False}
        assert _progress(event)["final"] is False

    def test_final_agent_message_is_durable(self):
        event = events.map_stream_event(
            _task(), AgentMessage(text="Implemented retries", final=True)
        )

        assert _text(event) == "Implemented retries"
        # The run's one durable message: NOT flagged ephemeral, so it persists.
        assert is_ephemeral_status_event(event) is False
        assert _data(event) == {"text": "Implemented retries", "final": True}

    def test_empty_agent_message_is_dropped(self):
        assert events.map_stream_event(_task(), AgentMessage(text="", final=False)) is None

    def test_executor_trace_is_dropped_without_warning(self):
        assert events.map_stream_event(_task(), ExecutorTrace(kind="reasoning", text="hmm")) is None
        assert events.map_stream_event(_task(), ExecutorTrace(kind="turn.completed")) is None

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


class TestStreamViewShapesCommandOutput:
    """Only `full` puts a command's output — the target repo's own content — on
    the wire. Every other view keeps the command and its exit code, which is
    what a progress renderer actually needs."""

    def _completed(self, view: str, **kwargs):
        return events.map_stream_event(
            _task(),
            CommandCompleted(call_id="c", command="cat secrets.env", **kwargs),
            view=view,
        )

    def test_full_view_carries_the_output(self):
        data = _data(self._completed(EVOLUTION_VIEW_FULL, output="TOKEN=abc"))
        assert data["output"] == "TOKEN=abc"
        assert data["truncated"] is False

    @pytest.mark.parametrize("view", [EVOLUTION_VIEW_ACTIVITY, EVOLUTION_VIEW_MILESTONES])
    def test_reduced_views_withhold_the_output(self, view):
        data = _data(self._completed(view, exit_code=0, output="TOKEN=abc"))

        assert "output" not in data
        # The command and its result still arrive — withholding output must not
        # cost the consumer the ability to render the step.
        assert data["command"] == "cat secrets.env"
        assert data["exitCode"] == 0

    def test_withheld_output_is_flagged_truncated(self):
        """`truncated` is how a consumer tells "printed nothing" from "printed
        something you were not sent"; the two are otherwise identical."""
        assert _data(self._completed(EVOLUTION_VIEW_ACTIVITY, output="TOKEN=abc"))["truncated"] is True

    def test_a_silent_command_is_not_flagged_truncated(self):
        assert _data(self._completed(EVOLUTION_VIEW_ACTIVITY))["truncated"] is False

    def test_default_view_withholds_the_output(self):
        """The mapper's default matches the directive's own default, so a caller
        that never mentions `view` is not sent the repository's file contents."""
        event = events.map_stream_event(
            _task(), CommandCompleted(call_id="c", command="cat x", output="body")
        )
        assert "output" not in _data(event)

    def test_view_does_not_change_which_events_are_ephemeral(self):
        """Views bound delivery; the ephemeral mark decides persistence. They
        must stay independent, or `milestones` would stop matching the record."""
        for view in (EVOLUTION_VIEW_FULL, EVOLUTION_VIEW_ACTIVITY, EVOLUTION_VIEW_MILESTONES):
            assert is_ephemeral_status_event(self._completed(view, output="x")) is True


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
        # Without a pull request the branch is the user's only pointer to the
        # work, so it is named — but the commit sha stays on the artifact.
        assert _text(terminal) == "Done — changes are on branch evolution/ctx-1"
        assert "abc1234" not in _text(terminal)

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
        # A pull request is somewhere the user can go, so it replaces the
        # branch name rather than joining it.
        assert _text(out[1]) == "Done — ready for review: https://github.com/acme/x/pull/7"
        assert "evolution/ctx-1" not in _text(out[1])

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
        assert _text(terminal) == "No changes were needed"

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
        # The diagnostic reaches the operator on the artifact...
        assert MessageToDict(artifact_event.artifact.parts[0].data)["error"] == "push denied"
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        # ...while the person who asked for the change reads a fixed sentence.
        # A toolkit failure is `str(exc)`, which for an executor error carries
        # CLI flags and a stderr tail.
        assert _text(terminal) == events._RUN_FAILED_TEXT
        assert "push denied" not in _text(terminal)

    def test_cancelled_emits_nothing(self):
        """The A2A cancel flow owns the terminal CANCELED event; a cancelled
        run must not race it with its own terminal status or artifact."""
        assert events.result_events(_task(), _result("cancelled")) == []

    def test_failed_with_rescue_reports_rescue_fields(self):
        """A failed run's rescue state travels on the result artifact: the
        client learns whether undelivered work was pushed to the evolution
        branch (durable, resume picks it up) or fell back to a pod-local
        bundle an operator must restore promptly."""
        out = events.result_events(
            _task(),
            _result(
                "failed",
                error="the remote end hung up",
                rescue_pushed=False,
                rescue_path="/data/rescue-ctx-1.bundle",
            ),
        )

        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["rescuePath"] == "/data/rescue-ctx-1.bundle"
        assert "rescuePushed" not in data or data["rescuePushed"] is False

        out = events.result_events(
            _task(), _result("failed", error="codex died", rescue_pushed=True)
        )
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["rescuePushed"] is True
        assert "rescuePath" not in data

    def test_result_without_rescue_fields_still_maps(self):
        """An older toolkit's result object has no rescue fields — the mapping
        must degrade to defaults instead of raising."""
        out = events.result_events(_task(), _result("failed", error="boom"))
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert "rescuePath" not in data


class TestDurableEventsAreNeverEphemeral:
    """Whatever ends the run has to survive in the record — the ephemeral flag
    is for live progress only."""

    def test_failed_event_is_durable(self):
        event = events.failed_event(_task(), error="clone failed: repository not found")
        assert is_ephemeral_status_event(event) is False
        assert event.status.state == TaskState.TASK_STATE_FAILED

    def test_terminal_result_status_is_durable(self):
        produced = events.result_events(
            _task(), _result(outcome="succeeded", branch="evolution/ctx-1")
        )
        statuses = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)]
        assert statuses, "a terminal status is expected alongside the artifact"
        assert all(is_ephemeral_status_event(e) is False for e in statuses)


class TestTerminalStageReachesDurableMetadata:
    """A status event's metadata is merged into `task.metadata` on persist, so
    the last persisted event carrying a `stage` decides what a finished run
    reports. Phase narration is ephemeral, so the terminal event has to."""

    def test_succeeded_terminal_reports_the_terminal_stage(self):
        produced = events.result_events(
            _task(), _result(outcome="succeeded", branch="evolution/ctx-1")
        )
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "succeeded"

    def test_failed_terminal_reports_the_terminal_stage(self):
        produced = events.result_events(_task(), _result(outcome="failed", error="boom"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "failed"

    def test_no_change_terminal_reports_the_terminal_stage(self):
        produced = events.result_events(_task(), _result(outcome="no_change"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "no_change"

    def test_a_finished_run_does_not_still_report_preparing(self):
        """The regression this guards: with phase narration ephemeral, the only
        persisted stage was the one branch resolution announced."""
        produced = events.result_events(_task(), _result(outcome="succeeded"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] != "preparing"
