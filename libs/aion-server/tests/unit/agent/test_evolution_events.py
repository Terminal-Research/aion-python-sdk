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


def _run_progress(scope: str = "auto") -> events.RunProgress:
    return events.RunProgress(scope=scope)


def _map_stream_event(task, event, *, progress=None, view=EVOLUTION_VIEW_ACTIVITY):
    """`events.map_stream_event` with a fresh accumulator unless one is given.

    Most tests here map a single event in isolation, where the accumulator is
    noise. The tests that are *about* accumulation (TestProgressAccumulates)
    thread one instance through several calls, which is how the handler uses it.
    """
    return events.map_stream_event(
        task, event, progress=progress or _run_progress(), view=view
    )


def _result_events(task, result, *, progress=None):
    return events.result_events(task, result, progress=progress or _run_progress())


def _result(outcome: str = "succeeded", **overrides):
    values = {
        "outcome": outcome,
        "branch": "evolution/ctx-1",
        "commit_sha": "abc1234",
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
        event = _map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="executing")))

        assert isinstance(event, TaskStatusUpdateEvent)
        assert event.status.state == TaskState.TASK_STATE_WORKING
        assert _text(event) == events._PHASE_TEXT["executing"]
        assert _text(event) != "executing"
        assert _progress(event) == {"scope": "auto", "stage": "executing"}
        # Narration of a phase the run has already left: streamed live, kept
        # out of the durable record. What happened in the phase survives on the
        # result artifact and the final summary.
        assert is_ephemeral_status_event(event) is True

    def test_unmapped_phase_falls_back_to_raw_token(self):
        event = _map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="brand-new")))
        assert _text(event) == "brand-new"
        assert _progress(event) == {"scope": "auto", "stage": "brand-new"}

    def test_branch_resolved_fresh(self):
        event = _map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=False)
        )

        # A fresh start is the default, and the branch name is plumbing: no
        # message at all. The structured facts still ride the event.
        assert not event.status.HasField("message")
        # BranchResolved is a fact within the PREPARING phase, not its own phase —
        # stage matches the toolkit's Phase.PREPARING, same as a bare PhaseStarted.
        assert _progress(event) == {
            "scope": "auto",
            "stage": "preparing",
            "branch": "evolution/ctx-1",
            "resumed": False,
            "priorCommits": 0,
        }
        # A persisted milestone, not live progress: it is the only record of
        # which branch a run that later fails was working on.
        assert is_ephemeral_status_event(event) is False

    def test_branch_resolved_resumed_names_prior_work(self):
        event = _map_stream_event(
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
        event = _map_stream_event(
            _task(), BranchResolved(branch="evolution/ctx-1", resumed=True, prior_commits=0)
        )
        assert _text(event) == "Picking up where the previous run left off"

    def test_command_started_is_ephemeral_typed_and_correlated(self):
        event = _map_stream_event(
            _task(), CommandStarted(call_id="call_1", command="pytest -q")
        )

        assert _text(event) == "running $ pytest -q"
        assert is_ephemeral_status_event(event) is True
        assert _part_schema(event) == BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1
        assert _data(event) == {"callId": "call_1", "command": "pytest -q"}
        # Nothing about this one command: `callId` and `command` are on the
        # payload above, and publishing them here too would make two sources
        # of one fact.
        assert _progress(event) == {"scope": "auto", "stage": "executing"}

    def test_command_completed_carries_exit_code_and_output(self):
        event = _map_stream_event(
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
        assert _progress(event) == {"scope": "auto", "stage": "executing"}

    def test_command_completed_nonzero_exit_is_flagged_in_text(self):
        event = _map_stream_event(
            _task(), CommandCompleted(call_id="c", command="pytest -q", exit_code=1)
        )
        assert _text(event) == "$ pytest -q (exit 1)"
        # The exit code is machine-readable on the payload, not in progress.
        assert _data(event)["exitCode"] == 1

    def test_command_completed_bounds_chatty_output_and_flags_truncated(self):
        big = "x" * (events._COMMAND_OUTPUT_TAIL_CHARS + 500)
        event = _map_stream_event(
            _task(),
            CommandCompleted(call_id="c", command="cat big.log", output=big),
            view=EVOLUTION_VIEW_FULL,
        )
        data = _data(event)
        assert len(data["output"]) == events._COMMAND_OUTPUT_TAIL_CHARS
        assert data["output"] == big[-events._COMMAND_OUTPUT_TAIL_CHARS:]
        assert data["truncated"] is True

    def test_command_completed_without_exit_code_omits_it(self):
        event = _map_stream_event(
            _task(), CommandCompleted(call_id="c", command="ls")
        )
        assert _text(event) == "$ ls"
        assert "exitCode" not in _data(event)
        assert "exitCode" not in _data(event)

    def test_intermediate_agent_message_is_ephemeral(self):
        event = _map_stream_event(_task(), AgentMessage(text="working", final=False))

        assert _text(event) == "working"
        assert is_ephemeral_status_event(event) is True
        assert _part_schema(event) == BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1
        # Both representations ride the event on purpose: prose for the user,
        # a typed payload for a consumer that does not parse prose.
        assert _data(event) == {"text": "working", "final": False}
        # `final` says something about this message, so it lives on the payload
        # and nowhere else; progress carries run-level state only.
        assert "final" not in _progress(event)

    def test_final_agent_message_is_durable(self):
        event = _map_stream_event(
            _task(), AgentMessage(text="Implemented retries", final=True)
        )

        assert _text(event) == "Implemented retries"
        # The run's one durable message: NOT flagged ephemeral, so it persists.
        assert is_ephemeral_status_event(event) is False
        assert _data(event) == {"text": "Implemented retries", "final": True}

    def test_empty_agent_message_is_dropped(self):
        assert _map_stream_event(_task(), AgentMessage(text="", final=False)) is None

    def test_executor_trace_is_dropped_without_warning(self):
        assert _map_stream_event(_task(), ExecutorTrace(kind="reasoning", text="hmm")) is None
        assert _map_stream_event(_task(), ExecutorTrace(kind="turn.completed")) is None

    def test_spec_captured_becomes_markdown_artifact(self):
        event = _map_stream_event(
            _task(), SpecCaptured(path=".aion/evolutions/ctx-1/retries.md", content="# Spec")
        )

        assert isinstance(event, TaskArtifactUpdateEvent)
        assert event.artifact.name == events.SPEC_ARTIFACT_NAME
        assert event.last_chunk is True
        assert event.artifact.parts[0].text == "# Spec"
        meta = MessageToDict(event.artifact.metadata)[events.PROGRESS_METADATA_KEY]
        assert meta == {"path": ".aion/evolutions/ctx-1/retries.md"}

    def test_unknown_event_types_are_dropped(self):
        assert _map_stream_event(_task(), SimpleNamespace()) is None


class TestStreamViewShapesCommandOutput:
    """Only `full` puts a command's output — the target repo's own content — on
    the wire. Every other view keeps the command and its exit code, which is
    what a progress renderer actually needs."""

    def _completed(self, view: str, **kwargs):
        return _map_stream_event(
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
        event = _map_stream_event(
            _task(), CommandCompleted(call_id="c", command="cat x", output="body")
        )
        assert "output" not in _data(event)

    def test_view_does_not_change_which_events_are_ephemeral(self):
        """Views bound delivery; the ephemeral mark decides persistence. They
        must stay independent, or `milestones` would stop matching the record."""
        for view in (EVOLUTION_VIEW_FULL, EVOLUTION_VIEW_ACTIVITY, EVOLUTION_VIEW_MILESTONES):
            assert is_ephemeral_status_event(self._completed(view, output="x")) is True


class TestEventKindDrift:
    """The comparison the handler runs against the toolkit installed beside it.

    Takes names rather than the toolkit's union, so it is exercised here without
    the toolkit — which is the point: the environments holding both halves are
    deployments, not test runs.
    """

    def test_agreement_is_no_drift(self):
        assert events.event_kind_drift(events._KNOWN_EVENT_KINDS) == set()

    def test_reports_a_kind_the_toolkit_added(self):
        """The mapper would drop it, and only say so once one had occurred."""
        kinds = set(events._KNOWN_EVENT_KINDS) | {"CheckpointReached"}
        assert events.event_kind_drift(kinds) == {"CheckpointReached"}

    def test_reports_a_kind_the_toolkit_dropped(self):
        """A branch that can never fire again is drift too — the symmetric
        difference catches it where a subset check would report nothing."""
        kinds = set(events._KNOWN_EVENT_KINDS) - {"SpecCaptured"}
        assert events.event_kind_drift(kinds) == {"SpecCaptured"}

    def test_reports_both_sides_of_a_rename(self):
        """The likeliest drift, and the one worth naming in full: the old name
        alone would not tell an operator what to map it onto."""
        kinds = (set(events._KNOWN_EVENT_KINDS) - {"AgentMessage"}) | {"ExecutorMessage"}
        assert events.event_kind_drift(kinds) == {"AgentMessage", "ExecutorMessage"}

    def test_accepts_any_iterable(self):
        """The handler passes whatever it built off `get_args`; a list of the
        same names must not read as drift."""
        assert events.event_kind_drift(list(events._KNOWN_EVENT_KINDS)) == set()


class TestEventKindDriftGuard:
    def test_known_event_kinds_cover_every_toolkit_event_type(self):
        """`events._KNOWN_EVENT_KINDS` must name every class the toolkit's
        `EvolutionEvent` union can carry. The deployment-side check
        (`event_kind_drift`, run by the handler at bind time) is what catches
        this where it matters; this is the same check for whoever has the
        optional toolkit installed, turning it into a test failure rather than a
        log line."""
        toolkit = pytest.importorskip("aion.toolkits.behaviour_evolution")
        from typing import get_args

        event_types = get_args(toolkit.EvolutionEvent)
        assert event_types, "EvolutionEvent resolved to no union members"
        names = {t.__name__ for t in event_types}
        assert names == events._KNOWN_EVENT_KINDS


class TestFailedEvent:
    def test_failed_state_and_message(self):
        event = events.failed_event(_task(), error="CODEX_PROVIDER is not set")
        assert event.status.state == TaskState.TASK_STATE_FAILED
        assert _text(event) == "CODEX_PROVIDER is not set"


class TestResultEvents:
    def test_succeeded_emits_schema_tagged_artifact_then_completed(self):
        out = _result_events(_task(), _result("succeeded"))

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
        assert _text(terminal) == (
            "Done — changes are on branch evolution/ctx-1."
        )
        assert "abc1234" not in _text(terminal)

    def test_resumed_run_reports_resumed_flag(self):
        out = _result_events(_task(), _result("succeeded", resumed=True))
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["resumed"] is True

    def test_pr_url_lands_in_payload_and_terminal_text(self):
        out = _result_events(
            _task(), _result("succeeded", pr_url="https://github.com/acme/x/pull/7")
        )
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["prUrl"] == "https://github.com/acme/x/pull/7"
        # A pull request is somewhere the user can go, so it replaces the
        # branch name rather than joining it.
        assert _text(out[1]) == (
            "Done — ready for review: https://github.com/acme/x/pull/7."
        )
        assert "evolution/ctx-1" not in _text(out[1])

    def test_executor_summary_precedes_the_location_line(self):
        out = _result_events(
            _task(),
            _result(
                "succeeded",
                pr_url="https://github.com/acme/x/pull/7",
                summary="Added a /test command node and wired it into the graph.",
            ),
        )
        assert _text(out[1]) == (
            "Added a /test command node and wired it into the graph.\n\n"
            "Done — ready for review: https://github.com/acme/x/pull/7."
        )

    def test_no_change_completes_with_explanation(self):
        out = _result_events(
            _task(),
            _result(
                "no_change", branch=None, commit_sha=None, commit_count=0,
                spec_path=None,
            ),
        )

        artifact_event, terminal = out
        assert MessageToDict(artifact_event.artifact.parts[0].data)["outcome"] == "no_change"
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        assert _text(terminal) == (
            "No changes were needed."
        )

    def test_failed_reports_error_in_artifact_and_status(self):
        out = _result_events(
            _task(),
            _result(
                "failed",
                branch=None,
                commit_sha=None,
                error="push denied",
                commit_count=None,
                spec_path=None,
            ),
        )

        artifact_event, terminal = out
        # The diagnostic reaches the operator on the artifact...
        assert (
            MessageToDict(artifact_event.artifact.parts[0].data)["error"]["details"]
            == "push denied"
        )
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        # ...while the person who asked for the change reads a short, safe
        # statement of what was (not) obtained. A toolkit failure is
        # `str(exc)`, which for an executor error carries CLI flags and a
        # stderr tail, so it never lands in this text.
        assert _text(terminal) == (
            "Failed — No changes were made."
        )
        assert "push denied" not in _text(terminal)

    def test_failed_with_rescued_branch_names_it(self):
        out = _result_events(
            _task(),
            _result(
                "failed",
                branch="evolution/ctx-1",
                commit_sha=None,
                error="push denied",
                commit_count=None,
                spec_path=None,
                rescue_pushed=True,
            ),
        )
        _, terminal = out
        assert _text(terminal) == (
            "Failed — Work completed so far is preserved on branch "
            "evolution/ctx-1."
        )

    def test_failed_with_rescue_bundle_points_to_operator(self):
        out = _result_events(
            _task(),
            _result(
                "failed",
                branch=None,
                commit_sha=None,
                error="push denied",
                commit_count=None,
                spec_path=None,
                rescue_path="/var/aion/rescue/ctx-1.bundle",
            ),
        )
        _, terminal = out
        assert _text(terminal) == (
            "Failed — Work completed so far was saved on the improver and "
            "needs an operator to restore it."
        )

    def test_failed_leads_with_error_reason_when_present(self):
        """The tool's own short, safe explanation — e.g. an unsupported
        model for the account — is worth more to the requester than a
        content-free 'the run could not be completed'."""
        out = _result_events(
            _task(),
            _result(
                "failed",
                branch=None,
                commit_sha=None,
                error=(
                    "codex exec ... failed (1): ... — codex reported: The "
                    "'gpt-5.6-lunsa' model is not supported when using Codex "
                    "with a ChatGPT account."
                ),
                error_reason=(
                    "The 'gpt-5.6-lunsa' model is not supported when using "
                    "Codex with a ChatGPT account."
                ),
                commit_count=None,
                spec_path=None,
            ),
        )
        artifact_event, terminal = out
        assert _text(terminal) == (
            "Failed — The 'gpt-5.6-lunsa' model is not supported when using "
            "Codex with a ChatGPT account. No changes were made."
        )
        # The raw diagnostic (CLI invocation, stderr) never lands in the
        # chat-facing text, even when a reason is available.
        assert "codex exec" not in _text(terminal)
        # ...while the full diagnostic and the reason both ride the artifact,
        # grouped under one `error` object.
        data = MessageToDict(artifact_event.artifact.parts[0].data)
        assert "codex exec" in data["error"]["details"]
        assert data["error"]["reason"] == (
            "The 'gpt-5.6-lunsa' model is not supported when using Codex "
            "with a ChatGPT account."
        )

    def test_failed_combines_reason_and_rescued_branch(self):
        out = _result_events(
            _task(),
            _result(
                "failed",
                branch="evolution/ctx-1",
                commit_sha=None,
                error="push denied — remote: protected branch",
                error_reason="The remote rejected the push: branch is protected.",
                commit_count=None,
                spec_path=None,
                rescue_pushed=True,
            ),
        )
        _, terminal = out
        assert _text(terminal) == (
            "Failed — The remote rejected the push: branch is protected. "
            "Work completed so far is preserved on branch evolution/ctx-1."
        )

    def test_cancelled_emits_nothing(self):
        """The A2A cancel flow owns the terminal CANCELED event; a cancelled
        run must not race it with its own terminal status or artifact."""
        assert _result_events(_task(), _result("cancelled")) == []

    def test_failed_with_rescue_reports_rescue_fields(self):
        """A failed run's rescue state travels on the result artifact: the
        client learns whether undelivered work was pushed to the evolution
        branch (durable, resume picks it up) or fell back to a pod-local
        bundle an operator must restore promptly."""
        out = _result_events(
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

        out = _result_events(
            _task(), _result("failed", error="codex died", rescue_pushed=True)
        )
        data = MessageToDict(out[0].artifact.parts[0].data)
        assert data["rescuePushed"] is True
        assert "rescuePath" not in data

    def test_result_without_rescue_fields_still_maps(self):
        """An older toolkit's result object has no rescue fields — the mapping
        must degrade to defaults instead of raising."""
        out = _result_events(_task(), _result("failed", error="boom"))
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
        produced = _result_events(
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
        produced = _result_events(
            _task(), _result(outcome="succeeded", branch="evolution/ctx-1")
        )
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "succeeded"

    def test_failed_terminal_reports_the_terminal_stage(self):
        produced = _result_events(_task(), _result(outcome="failed", error="boom"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "failed"

    def test_no_change_terminal_reports_the_terminal_stage(self):
        produced = _result_events(_task(), _result(outcome="no_change"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
        assert _progress(terminal)["outcome"] == "no_change"

    def test_a_finished_run_does_not_still_report_preparing(self):
        """The regression this guards: with phase narration ephemeral, the only
        persisted stage was the one branch resolution announced."""
        produced = _result_events(_task(), _result(outcome="succeeded"))
        terminal = [e for e in produced if isinstance(e, TaskStatusUpdateEvent)][-1]
        assert _progress(terminal)["stage"] != "preparing"


class TestProgressAccumulates:
    """The progress struct is a running snapshot, not a per-event delta.

    `Task.metadata` is updated with protobuf's `MergeFrom`, which replaces a
    Struct-valued key wholesale instead of merging into it. A delta would
    therefore leave the durable record describing only the last persisted
    event.
    """

    def _persisted_metadata(self, events_in_order) -> dict:
        """What `task.metadata` holds after persisting `events_in_order`.

        Mirrors the task manager: only non-ephemeral status events are
        persisted, and each one is merged exactly as the vendored SDK does.
        """
        task = _task()
        for event in events_in_order:
            if isinstance(event, TaskStatusUpdateEvent) and not is_ephemeral_status_event(event):
                task.metadata.MergeFrom(event.metadata)
        return MessageToDict(task.metadata)[events.PROGRESS_METADATA_KEY]

    def test_branch_survives_to_the_end_of_the_run(self):
        """The regression: a run's branch was resolved early and overwritten by
        every later persisted event, so a finished task no longer recorded
        which branch it had worked on."""
        progress = _run_progress()
        task = _task()
        stream = [
            _map_stream_event(task, PhaseStarted(phase=SimpleNamespace(value="preparing")), progress=progress),
            _map_stream_event(task, BranchResolved(branch="evolution/ctx-1", resumed=True, prior_commits=2), progress=progress),
            _map_stream_event(task, AgentMessage(text="done here", final=True), progress=progress),
        ]
        stream += _result_events(task, _result("succeeded"), progress=progress)

        persisted = self._persisted_metadata(stream)
        assert persisted["branch"] == "evolution/ctx-1"
        assert persisted["resumed"] is True
        assert persisted["stage"] == events._TERMINAL_STAGE
        assert persisted["outcome"] == "succeeded"

    def test_progress_never_carries_per_event_facts(self):
        """Progress is run-level state only. Facts about a single event live on
        that event's typed payload and nowhere else — two published sources for
        one fact is two things to maintain and two things that can disagree."""
        progress = _run_progress()
        task = _task()
        per_event = {"callId", "exitCode", "executorKind", "final", "command", "output"}
        produced = [
            _map_stream_event(task, CommandStarted(call_id="c1", command="pytest"), progress=progress),
            _map_stream_event(task, CommandCompleted(call_id="c1", command="pytest", exit_code=1), progress=progress),
            _map_stream_event(task, AgentMessage(text="done", final=True), progress=progress),
        ]
        produced += [
            e for e in _result_events(task, _result("succeeded"), progress=progress)
            if isinstance(e, TaskStatusUpdateEvent)
        ]

        for event in produced:
            assert not (per_event & _progress(event).keys()), _progress(event)

    def test_scope_rides_every_event(self):
        """A consumer that did not author the directive cannot otherwise tell a
        planning run from an implementing one — the stages are identical."""
        progress = _run_progress("plan")
        task = _task()
        first = _map_stream_event(task, PhaseStarted(phase=SimpleNamespace(value="preparing")), progress=progress)
        terminal = [
            e for e in _result_events(task, _result("no_change"), progress=progress)
            if isinstance(e, TaskStatusUpdateEvent)
        ][-1]

        assert _progress(first)["scope"] == "plan"
        assert _progress(terminal)["scope"] == "plan"


class TestUsageReachesTheCaller:
    def _usage(self, **overrides):
        values = {"input_tokens": 1200, "output_tokens": 300, "requests": 2, "wall_clock_s": 4.5}
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_usage_lands_on_the_result_artifact(self):
        out = _result_events(_task(), _result("succeeded", usage=self._usage()))
        artifact = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)][0]
        usage = MessageToDict(artifact.artifact.parts[0].data)["usage"]

        assert usage["inputTokens"] == 1200
        assert usage["outputTokens"] == 300
        assert usage["totalTokens"] == 1500
        assert usage["requests"] == 2
        assert usage["wallClockSeconds"] == 4.5

    def test_usage_also_reaches_the_durable_progress_struct(self):
        """The progress struct is the only one of the two that reaches
        `task.metadata`, so cost is auditable without opening the artifact."""
        out = _result_events(_task(), _result("succeeded", usage=self._usage()))
        terminal = [e for e in out if isinstance(e, TaskStatusUpdateEvent)][-1]

        assert _progress(terminal)["usage"]["totalTokens"] == 1500

    def test_unreported_usage_is_absent_rather_than_zero(self):
        """A row of zeros would claim the run was free; absence says unknown."""
        out = _result_events(_task(), _result("succeeded"))
        artifact = [e for e in out if isinstance(e, TaskArtifactUpdateEvent)][0]

        assert "usage" not in MessageToDict(artifact.artifact.parts[0].data)


class TestArtifactIdsAreStable:
    def test_respec_supersedes_rather_than_accumulates(self):
        """`append_artifact_to_task` replaces only on a matching id, so a random
        id per emission would pile up duplicate spec artifacts on a resume."""
        from a2a.server.tasks.task_manager import append_artifact_to_task

        task = _task()
        first = events.spec_artifact_event(task, path="spec.md", content="v1")
        second = events.spec_artifact_event(task, path="spec.md", content="v2")
        append_artifact_to_task(task, first)
        append_artifact_to_task(task, second)

        assert len(task.artifacts) == 1
        assert task.artifacts[0].parts[0].text == "v2"

    def test_result_artifact_id_is_stable_too(self):
        one = _result_events(_task(), _result("succeeded"))[0]
        two = _result_events(_task(), _result("succeeded"))[0]
        assert one.artifact.artifact_id == two.artifact.artifact_id

    def test_different_tasks_keep_their_own_artifacts(self):
        other = Task(id="task-2", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        mine = events.spec_artifact_event(_task(), path="spec.md", content="v1")
        theirs = events.spec_artifact_event(other, path="spec.md", content="v1")
        assert mine.artifact.artifact_id != theirs.artifact.artifact_id


class TestCancelResultMessage:
    def test_rescued_run_names_the_branch_and_carries_the_payload(self):
        message = events.cancel_result_message(
            _task(), _result("cancelled", rescue_pushed=True, branch="evolution/ctx-1")
        )
        assert "evolution/ctx-1" in message.parts[0].text
        payload = MessageToDict(message.parts[1].data)
        assert payload["outcome"] == "cancelled"
        assert payload["rescuePushed"] is True
        schema = MessageToDict(message.parts[1].metadata)[EVENT_EXTENSION_URI_V1]["schema"]
        assert schema == BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1

    def test_bundle_path_stays_out_of_the_prose(self):
        """The bundle is a path on the improver's own filesystem: an operator
        acts on it, the caller cannot, so it rides the payload only."""
        message = events.cancel_result_message(
            _task(),
            _result("cancelled", rescue_pushed=False, branch=None, rescue_path="/tmp/rescue.bundle"),
        )
        assert "/tmp/rescue.bundle" not in message.parts[0].text
        assert MessageToDict(message.parts[1].data)["rescuePath"] == "/tmp/rescue.bundle"

    def test_nothing_to_rescue_still_reports_the_outcome(self):
        message = events.cancel_result_message(_task(), _result("cancelled", branch=None))
        assert message.parts[0].text == "Cancelled."
        assert MessageToDict(message.parts[1].data)["outcome"] == "cancelled"


class TestStageIsOurVocabularyNotTheToolkitEnum:
    def test_known_phases_map_to_published_stages(self):
        for phase, stage in events._STAGE_BY_PHASE.items():
            event = _map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value=phase)))
            assert _progress(event)["stage"] == stage

    def test_unmapped_phase_passes_through_and_is_logged(self, caplog):
        """A phase a future toolkit adds must stay visible to a caller, but it
        is not part of the published vocabulary until it is named here — so it
        surfaces in the log as drift rather than silently becoming contract."""
        with caplog.at_level("WARNING"):
            event = _map_stream_event(_task(), PhaseStarted(phase=SimpleNamespace(value="verifying")))

        assert _progress(event)["stage"] == "verifying"
        assert "verifying" in caplog.text

    def test_terminal_stage_is_published_but_never_announced(self):
        """The toolkit never emits a PhaseStarted for its terminal phase, so
        nothing in the stream would move `stage` off the last one it did."""
        assert events._TERMINAL_STAGE not in events._STAGE_BY_PHASE
        terminal = [
            e for e in _result_events(_task(), _result("succeeded"))
            if isinstance(e, TaskStatusUpdateEvent)
        ][-1]
        assert _progress(terminal)["stage"] == events._TERMINAL_STAGE
