"""Pure mappers from toolkit run events/result DTOs onto A2A task events.

Toolkit-free at runtime: the toolkit's typed events are discriminated by
class name and read duck-typed (phase.value, branch, call_id, command, ...), so
this module — and its tests — work without the optional toolkit installed. The
toolkit owns all executor-stream parsing (its `CodexEventClassifier` turns
codex's `--json` envelopes into typed events); this module never sees a raw
codex event.

Three axes govern how an event crosses to A2A:

- *Typing.* The executor-stream events (`CommandStarted`, `CommandCompleted`,
  `AgentMessage`) are carried as schema-tagged data parts on the status
  message, using aion-core's published payload models — the same
  schema-tagging as the result artifact, so a programmatic consumer reads a
  typed part rather than parsing text. Every progress status also carries a
  machine-readable struct in the event `metadata` under `PROGRESS_METADATA_KEY`
  (`stage`, plus stage-specific fields). The human-facing text on the same
  event is what the end user sees.

- *Persistence.* Live progress — running commands, their results, intermediate
  agent messages — is flagged ephemeral: streamed to the client but dropped
  from task history by the task manager. Only milestones persist: branch
  resolution, the executor's final summary (`AgentMessage(final=True)`), the
  terminal status, and the `evolution-spec`/`evolution-result` artifacts.

- *Delivery.* The directive's `view` says how much of that the caller wants:
  `full` (everything, including each command's output), `activity` (the same
  chronicle minus the output — the default), `milestones` (only the durable
  events). This module shapes `full` vs `activity`, because dropping a payload
  field is the producer's job; `milestones` is the handler dropping whatever
  this module already flagged ephemeral, so the reduced view cannot drift from
  what the task record keeps.

The result artifact reuses aion-core's EvolutionResultActionPayload so the
outbound shape is the one the extension spec already defines, schema-tagged on
the part the same way inbound event parts are. The captured spec document
ships as its own markdown artifact (`SPEC_ARTIFACT_NAME`) — it is the durable
record of what the evolution planned/did/decided, readable without cloning.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Optional

from a2a.helpers import new_data_artifact_update_event, new_data_part
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from aion.core.a2a.extensions.behaviour_evolution import (
    EVOLUTION_VIEW_ACTIVITY,
    EVOLUTION_VIEW_FULL,
    EvolutionAgentMessagePayload,
    EvolutionCommandCompletedPayload,
    EvolutionCommandStartedPayload,
    EvolutionResultActionPayload,
)
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.server.a2a.utils import mark_status_event_ephemeral

if TYPE_CHECKING:
    from aion.toolkits.behaviour_evolution import EvolutionResult

logger = logging.getLogger(__name__)

__all__ = [
    "PROGRESS_METADATA_KEY",
    "RESULT_ARTIFACT_NAME",
    "SPEC_ARTIFACT_NAME",
    "failed_event",
    "map_stream_event",
    "result_events",
    "status_event",
]

RESULT_ARTIFACT_NAME = "evolution-result"
SPEC_ARTIFACT_NAME = "evolution-spec"

# Key in TaskStatusUpdateEvent.metadata carrying the machine-readable progress
# struct. Namespaced by the extension URI so it can't collide with other
# metadata producers.
PROGRESS_METADATA_KEY = BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

# Bound on the command output tail carried on the completed-command payload and
# progress metadata, so a chatty command can't bloat a status event. Head+tail
# would need parsing; the tail alone is what shows a failure's cause.
_COMMAND_OUTPUT_TAIL_CHARS = 2000

# Every class name `models.EvolutionEvent` can carry, kept in sync with the
# toolkit by the drift-guard test in test_evolution_events.py (skipped when the
# optional toolkit isn't installed). `map_stream_event` discriminates by name
# rather than `isinstance` because the toolkit is an optional dependency this
# module must work without — see the module docstring. `RunCompleted` is listed
# so it maps to None without a spurious warning: it carries no progress to
# surface (the handler reads the terminal result from `worker.result`, not off
# the stream), yet it is a known, expected member of the event union.
# `ExecutorTrace` is likewise known-but-dropped: reasoning, turn accounting and
# stream framing stay in the executor's logs, not in the A2A stream.
_KNOWN_EVENT_KINDS = frozenset(
    {
        "PhaseStarted",
        "BranchResolved",
        "CommandStarted",
        "CommandCompleted",
        "AgentMessage",
        "ExecutorTrace",
        "SpecCaptured",
        "RunCompleted",
    }
)

# Human-facing text for each run phase. The toolkit emits the phase as a typed
# enum (a structured fact, like BranchResolved's fields); phrasing them is a
# presentation concern that lives here, not in the domain executor — so tone,
# wording and localization stay in one place and a second consumer of the
# toolkit isn't bound to this UX. The raw phase token still travels on the
# progress struct's `stage` (what machines branch on); this is only the sentence
# a plain chat/A2A client shows the user. An unmapped phase falls back to its
# raw token — a new toolkit phase degrades gracefully instead of vanishing.
# Stage reported on the terminal event. Matches the toolkit's own terminal
# Phase token, so a consumer branching on `stage` sees the same vocabulary the
# stream used rather than a name invented here.
_TERMINAL_STAGE = "reporting"

# Names the effect on the user's work, not the mechanism that produces it: a
# workspace, a clone, a branch and a commit are means the user never asked for
# and cannot act on. Everything an operator or a UI needs is already carried
# structurally — `branch`/`resumed`/`priorCommits` on the progress struct that
# reaches `task.metadata`, and branch/sha/PR on the result artifact — so none
# of it has to be repeated in prose.
#
# `reporting` is absent on purpose: the toolkit sets that phase on its worker
# state but never emits a PhaseStarted for it, and the terminal message follows
# immediately anyway. An unmapped phase still falls back to its raw token, so a
# phase a future toolkit adds degrades to something visible rather than nothing.
_PHASE_TEXT = {
    "preparing": "Preparing to work on your project",
    "executing": "Working on the change — planning, editing, and checking the result",
    "delivering": "Saving the result",
}

# What the user reads when a run fails. Fixed and short by design: the toolkit
# reports failures as the exception string (`ctx.fail(str(exc))`), which for an
# executor failure carries the CLI flags it was invoked with and up to 1500
# characters of its stderr. That belongs to whoever is debugging the deployment,
# and reaches them intact on the result artifact's `error` field — not to the
# person who asked for a change.
_RUN_FAILED_TEXT = "The run could not be completed. See the run result for details."


def status_event(
    task: Task,
    *,
    state: "TaskState.ValueType",
    text: str | None = None,
    progress: dict | None = None,
    ephemeral: bool = False,
) -> TaskStatusUpdateEvent:
    """A status update for the task: optional agent message (what the end user
    reads) plus optional machine-readable `progress` struct (what consumers
    branch on).

    When `ephemeral`, the update is streamed to the client but kept out of task
    history (see `mark_status_event_ephemeral`). Never flag a terminal state
    ephemeral — that is the record of how the run ended.
    """
    status = TaskStatus(state=state)
    if text:
        status.message.CopyFrom(
            Message(
                context_id=task.context_id,
                task_id=task.id,
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            )
        )
    event = TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=status,
    )
    if progress:
        event.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update(progress)
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def failed_event(task: Task, *, error: str) -> TaskStatusUpdateEvent:
    """Terminal FAILED update carrying the user-facing error message."""
    return status_event(task, state=TaskState.TASK_STATE_FAILED, text=error)


def map_stream_event(
    task: Task, event: object, *, view: str = EVOLUTION_VIEW_ACTIVITY
) -> Optional[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
    """Map one toolkit stream event onto an A2A event, or None to drop it.

    Discriminates by class name so the toolkit stays an optional dependency.
    `RunCompleted` is deliberately not handled here — the handler owns the
    terminal mapping via `result_events`. `ExecutorTrace` maps to None (dropped)
    without a warning: it is the toolkit's escape hatch for reasoning / stream
    framing this module has no reason to surface.

    `view` is the caller's requested detail level. Only `EVOLUTION_VIEW_FULL`
    puts a command's output on the wire; every other view (including
    `milestones`, whose events the handler drops afterwards anyway) shapes the
    payload without it. The default matches the directive's own default, so a
    caller that never mentions `view` does not get the repository's file
    contents streamed to it.
    """
    kind = type(event).__name__
    if kind == "PhaseStarted":
        phase = event.phase.value
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=_PHASE_TEXT.get(phase, phase),
            progress={"stage": phase},
            # Narration of a phase the run has already left: live progress, not
            # a fact the durable record needs. What actually happened in each
            # phase survives in the result artifact and the final summary, so
            # persisting these would only pad the history of every evolution
            # sharing a context.
            ephemeral=True,
        )
    if kind == "BranchResolved":
        # Only a resume is worth a sentence: that this continues earlier work is
        # the one thing the user cannot infer, and it changes what they should
        # expect back. A fresh start is the default and its branch name is
        # plumbing, so that case ships no message at all — the event still
        # carries its progress struct, so the branch reaches `task.metadata`
        # either way and stays available to a UI or an operator.
        text = None
        if event.resumed:
            text = "Picking up where the previous run left off"
            if event.prior_commits:
                text = f"{text} — {event.prior_commits} change(s) already made"
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=text,
            progress={
                # BranchResolved is a fact *within* the PREPARING phase (see
                # worker._drive), not a phase of its own — `stage` names the
                # toolkit's actual Phase so it never claims more phases exist
                # than PhaseStarted ever announces. A consumer distinguishes
                # this from a bare PhaseStarted(preparing) by the `branch` key,
                # same pattern as `executorKind` under stage="executing".
                "stage": "preparing",
                "branch": event.branch,
                "resumed": event.resumed,
                "priorCommits": event.prior_commits,
            },
            # Deliberately NOT ephemeral: branch resolution is one of the
            # milestones this module's contract says the task record keeps (see
            # the module docstring). It is also the only record of *which*
            # branch a run that later fails was working on.
        )
    if kind == "CommandStarted":
        return _command_started_event(task, event)
    if kind == "CommandCompleted":
        return _command_completed_event(task, event, view=view)
    if kind == "AgentMessage":
        return _agent_message_event(task, event)
    if kind == "SpecCaptured":
        return spec_artifact_event(task, path=event.path, content=event.content)
    if kind not in _KNOWN_EVENT_KINDS:
        # A toolkit event type this mapper has never heard of: either the
        # toolkit added one (this module needs a branch for it) or the two
        # sides drifted apart. Silent drop would look identical to a
        # deliberately-unsurfaced kind (e.g. ExecutorTrace) — log it so drift is
        # visible in production, not just in the drift-guard test.
        logger.warning("evolution: unmapped toolkit stream event %r dropped", kind)
    return None


def _typed_status_event(
    task: Task,
    *,
    text: str,
    payload,
    schema: str,
    progress: dict,
    ephemeral: bool,
) -> TaskStatusUpdateEvent:
    """A WORKING status update carrying human-facing `text`, a schema-tagged
    data part for the typed `payload`, and the machine-readable `progress`
    struct.

    The data part is schema-tagged under `EVENT_EXTENSION_URI_V1` exactly like
    the result artifact's part, so a programmatic consumer reads a typed payload
    off the message instead of parsing text. When `ephemeral`, the event is
    flagged so the task manager streams it to the client but keeps it out of
    task history (see `mark_status_event_ephemeral`).
    """
    data_part = new_data_part(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    data_part.metadata.get_or_create_struct(EVENT_EXTENSION_URI_V1).update({"schema": schema})
    status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
    status.message.CopyFrom(
        Message(
            context_id=task.context_id,
            task_id=task.id,
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=[Part(text=text), data_part],
        )
    )
    event = TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=status)
    event.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update(progress)
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def _exit_code(event: object) -> Optional[int]:
    """The command's exit code read duck-typed, or None. `bool` is an `int`
    subclass — a stray boolean is not an exit code."""
    code = getattr(event, "exit_code", None)
    if isinstance(code, bool):
        return None
    return code if isinstance(code, int) else None


def _bounded_output(event: object) -> tuple[Optional[str], bool]:
    """The command's output tail (bounded to `_COMMAND_OUTPUT_TAIL_CHARS`) and
    whether it — or the executor upstream — was truncated. A chatty command
    must not bloat a status event; the tail is where a failure's cause shows."""
    upstream_truncated = bool(getattr(event, "truncated", False))
    output = getattr(event, "output", None)
    if not isinstance(output, str) or not output.strip():
        return None, upstream_truncated
    stripped = output.strip()
    if len(stripped) > _COMMAND_OUTPUT_TAIL_CHARS:
        return stripped[-_COMMAND_OUTPUT_TAIL_CHARS:], True
    return stripped, upstream_truncated


def _command_started_event(task: Task, event: object) -> TaskStatusUpdateEvent:
    """A command the executor began — ephemeral live progress."""
    return _typed_status_event(
        task,
        text=f"running $ {event.command}",
        payload=EvolutionCommandStartedPayload(call_id=event.call_id, command=event.command),
        schema=BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1,
        progress={"stage": "executing", "executorKind": "command_execution", "callId": event.call_id},
        ephemeral=True,
    )


def _command_completed_event(
    task: Task, event: object, *, view: str = EVOLUTION_VIEW_ACTIVITY
) -> TaskStatusUpdateEvent:
    """A command the executor finished, with its result — ephemeral live
    progress. A non-zero exit is called out in the human-facing text; the exit
    code and bounded output ride the typed payload and the progress struct.

    Outside `EVOLUTION_VIEW_FULL` the output is withheld: it is the target
    repository's content, and a caller rendering progress needs the command and
    its exit code, not what it printed. `truncated` then stays True whenever
    there *was* output, so a consumer can tell "produced nothing" from "produced
    something you were not sent" — the two are otherwise identical on the wire.
    """
    exit_code = _exit_code(event)
    output, truncated = _bounded_output(event)
    if view != EVOLUTION_VIEW_FULL:
        truncated = truncated or output is not None
        output = None
    text = f"$ {event.command}"
    if exit_code is not None and exit_code != 0:
        text = f"{text} (exit {exit_code})"
    progress: dict = {
        "stage": "executing",
        "executorKind": "command_execution",
        "callId": event.call_id,
    }
    if exit_code is not None:
        progress["exitCode"] = exit_code
    # The output itself rides the typed payload only. Repeating it here would
    # put the same (already bounded) text on the wire a second time for every
    # command the executor runs, which is the bulk of a run's traffic.
    return _typed_status_event(
        task,
        text=text,
        payload=EvolutionCommandCompletedPayload(
            call_id=event.call_id,
            command=event.command,
            exit_code=exit_code,
            output=output,
            truncated=truncated,
        ),
        schema=BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1,
        progress=progress,
        ephemeral=True,
    )


def _agent_message_event(task: Task, event: object) -> Optional[TaskStatusUpdateEvent]:
    """A natural-language message from the executor. The final summary
    (`final=True`) is the run's one durable message; intermediate messages are
    ephemeral live progress. A text-less message is dropped."""
    if not event.text:
        return None
    final = bool(getattr(event, "final", False))
    return _typed_status_event(
        task,
        text=event.text,
        payload=EvolutionAgentMessagePayload(text=event.text, final=final),
        schema=BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1,
        progress={"stage": "executing", "executorKind": "agent_message", "final": final},
        ephemeral=not final,
    )


def spec_artifact_event(task: Task, *, path: str, content: str) -> TaskArtifactUpdateEvent:
    """The evolution's spec document as a markdown artifact.

    Re-emitted on every run of the evolution (same name, fresh content) so the
    latest artifact always reflects the spec as committed — enough to restore
    the task's context without cloning the repo.
    """
    event = TaskArtifactUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        artifact=Artifact(
            artifact_id=str(uuid.uuid4()),
            name=SPEC_ARTIFACT_NAME,
            description=f"evolution spec ({path})",
            parts=[Part(text=content)],
        ),
        last_chunk=True,
    )
    event.artifact.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update({"path": path})
    return event


def _result_artifact_event(task: Task, result: "EvolutionResult") -> TaskArtifactUpdateEvent:
    """The result artifact alone, schema-tagged, paired with a terminal status
    event by `result_events`."""
    payload = EvolutionResultActionPayload(
        outcome=result.outcome,
        branch=result.branch,
        commit_sha=result.commit_sha,
        diff_summary=result.diff_summary,
        error=result.error,
        resumed=getattr(result, "resumed", False),
        commit_count=getattr(result, "commit_count", None),
        pr_url=getattr(result, "pr_url", None),
        spec_path=getattr(result, "spec_path", None),
        rescue_pushed=getattr(result, "rescue_pushed", False),
        rescue_path=getattr(result, "rescue_path", None),
    )
    artifact_event = new_data_artifact_update_event(
        task_id=task.id,
        context_id=task.context_id,
        name=RESULT_ARTIFACT_NAME,
        data=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
        last_chunk=True,
    )
    artifact_event.artifact.parts[0].metadata.get_or_create_struct(
        EVENT_EXTENSION_URI_V1
    ).update({"schema": EvolutionResultActionPayload.SCHEMA_URI})
    return artifact_event


def result_events(
    task: Task,
    result: "EvolutionResult",
) -> list[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """Map a terminal EvolutionResult onto the result artifact + terminal status.

    A cancelled run maps to no events at all: cancellation only ever comes
    from the A2A cancel flow, and the executor's TaskUpdater.cancel() already
    owns the terminal CANCELED event - emitting a second terminal here would
    race it (the CANCELED is published before the worker finishes unwinding,
    so even an artifact yielded here could land after the terminal). A
    cancelled run's undelivered commits are still durable without any event:
    the toolkit's rescue pushes the evolution branch, and the next run of the
    context resumes on it.
    """
    if result.outcome == "cancelled":
        return []

    # A status event's metadata is merged into `task.metadata` on persist, so
    # `stage` there is only ever as current as the last *persisted* event that
    # carried one. Phase narration is ephemeral and carries none, so without a
    # stage here the durable metadata of a finished run would still read
    # "preparing" — the phase its branch resolution announced.
    terminal_progress = {"stage": _TERMINAL_STAGE, "outcome": result.outcome}

    artifact_event = _result_artifact_event(task, result)
    if result.outcome == "failed":
        # `result.error` rides the artifact above, where an operator reads it.
        terminal = failed_event(task, error=_RUN_FAILED_TEXT)
        terminal.metadata.get_or_create_struct(PROGRESS_METADATA_KEY).update(
            terminal_progress
        )
    elif result.outcome == "no_change":
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text="No changes were needed",
            progress=terminal_progress,
        )
    else:
        # Where the work can be found, in the terms the user can act on. A pull
        # request is a place to go; without one the branch is the only pointer
        # they have, so it is named — as a location, not as a git operation.
        # The commit sha is omitted: it identifies the work for machines, and
        # the artifact already carries it.
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            text = f"Done — ready for review: {pr_url}"
        elif result.branch:
            text = f"Done — changes are on branch {result.branch}"
        else:
            text = "Done"
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text=text,
            progress=terminal_progress,
        )
    return [artifact_event, terminal]
