"""Pure mappers from toolkit run events/result DTOs onto A2A task events.

Toolkit-free at runtime: the toolkit's typed events are discriminated by
class name and read duck-typed (phase.value, branch, kind, ...), so this
module — and its tests — work without the optional toolkit installed.

Every progress status carries a machine-readable struct in the A2A event's
`metadata` under `PROGRESS_METADATA_KEY` (`stage`, plus stage-specific
fields), so downstream consumers track where the run is and attach
post-processing per stage without parsing the human-facing text. The
human-facing text on the same event is what the end user sees.

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

from a2a.helpers import new_data_artifact_update_event
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
from aion.core.a2a.extensions.behaviour_evolution import EvolutionResultActionPayload
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
)

from .directive import GATE_METADATA_KEY

if TYPE_CHECKING:
    from aion.toolkits.behaviour_evolution import EvolutionResult

logger = logging.getLogger(__name__)

__all__ = [
    "PROGRESS_METADATA_KEY",
    "RESULT_ARTIFACT_NAME",
    "SPEC_ARTIFACT_NAME",
    "SURFACED_EXECUTOR_KINDS",
    "failed_event",
    "map_stream_event",
    "plan_gate_events",
    "result_events",
    "status_event",
]

RESULT_ARTIFACT_NAME = "evolution-result"
SPEC_ARTIFACT_NAME = "evolution-spec"

# Key in TaskStatusUpdateEvent.metadata carrying the machine-readable progress
# struct. Namespaced by the extension URI so it can't collide with other
# metadata producers.
PROGRESS_METADATA_KEY = BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

# Executor stream kinds surfaced to the user as WORKING updates. The rest
# (reasoning deltas, stream framing, token accounting) stay in logs — extend
# this set, or post-process on the metadata's `executorKind`, to change what
# the end user sees.
SURFACED_EXECUTOR_KINDS = frozenset({"agent_message", "command_execution"})

# Every class name `models.EvolutionEvent` can carry, kept in sync with the
# toolkit by the drift-guard test in test_evolution_events.py (skipped when the
# optional toolkit isn't installed). `map_stream_event` discriminates by name
# rather than `isinstance` because the toolkit is an optional dependency this
# module must work without — see the module docstring. `RunCompleted` is listed
# so it maps to None without a spurious warning: it carries no progress to
# surface (the handler reads the terminal result from `worker.result`, not off
# the stream), yet it is a known, expected member of the event union.
_KNOWN_EVENT_KINDS = frozenset(
    {"PhaseStarted", "BranchResolved", "ExecutorEvent", "SpecCaptured", "RunCompleted"}
)


def status_event(
    task: Task,
    *,
    state: "TaskState.ValueType",
    text: str | None = None,
    progress: dict | None = None,
) -> TaskStatusUpdateEvent:
    """A status update for the task: optional agent message (what the end user
    reads) plus optional machine-readable `progress` struct (what consumers
    branch on)."""
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
    return event


def failed_event(task: Task, *, error: str) -> TaskStatusUpdateEvent:
    """Terminal FAILED update carrying the user-facing error message."""
    return status_event(task, state=TaskState.TASK_STATE_FAILED, text=error)


def map_stream_event(
    task: Task, event: object
) -> Optional[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
    """Map one toolkit stream event onto an A2A event, or None to drop it.

    Discriminates by class name so the toolkit stays an optional dependency.
    `RunCompleted` is deliberately not handled here — the handler owns the
    terminal mapping via `result_events`.
    """
    kind = type(event).__name__
    if kind == "PhaseStarted":
        phase = event.phase.value
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=phase,
            progress={"stage": phase},
        )
    if kind == "BranchResolved":
        if event.resumed:
            text = (
                f"resuming evolution on {event.branch} "
                f"({event.prior_commits} commit(s) from earlier runs)"
            )
        else:
            text = f"started evolution branch {event.branch}"
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=text,
            progress={
                "stage": "branch",
                "branch": event.branch,
                "resumed": event.resumed,
                "priorCommits": event.prior_commits,
            },
        )
    if kind == "ExecutorEvent":
        if event.kind not in SURFACED_EXECUTOR_KINDS or not event.text:
            return None
        text = event.text if event.kind == "agent_message" else f"$ {event.text}"
        return status_event(
            task,
            state=TaskState.TASK_STATE_WORKING,
            text=text,
            progress={"stage": "executing", "executorKind": event.kind},
        )
    if kind == "SpecCaptured":
        return spec_artifact_event(task, path=event.path, content=event.content)
    if kind not in _KNOWN_EVENT_KINDS:
        # A toolkit event type this mapper has never heard of: either the
        # toolkit added one (this module needs a branch for it) or the two
        # sides drifted apart. Silent drop would look identical to a
        # deliberately-unsurfaced kind (e.g. a filtered ExecutorEvent) — log
        # it so drift is visible in production, not just in the drift-guard
        # test.
        logger.warning("evolution: unmapped toolkit stream event %r dropped", kind)
    return None


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
    """The result artifact alone, schema-tagged — shared by `result_events` and
    `plan_gate_events`, which each pair it with a different terminal event."""
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
    ).update({"schema": BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1})
    return artifact_event


def plan_gate_events(
    task: Task,
    result: "EvolutionResult",
    *,
    stash: dict,
) -> list[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """Terminal mapping for a plan-stage run of a gated evolution.

    A successful planning run does not complete the task — it pauses it:
    INPUT_REQUIRED with the review prompt, the result artifact for context,
    and the directive stash attached as event metadata so the TaskManager
    merges it into Task.metadata (what `parse_gated_resume` reads back on the
    reviewer's reply). The spec itself was already emitted as the spec
    artifact by the stream mapping.

    A planning run that produced nothing reviewable (no_change: the executor
    never committed a spec) fails the task explicitly rather than pausing on
    an empty gate. failed/cancelled map exactly as ungated runs do.
    """
    if result.outcome == "no_change":
        return [
            failed_event(
                task,
                error=(
                    "planning run produced no spec to review - the evolution "
                    "cannot be gated on an empty plan; retry with a refined "
                    "instruction"
                ),
            )
        ]
    if result.outcome != "succeeded":
        return result_events(task, result)

    gate = status_event(
        task,
        state=TaskState.TASK_STATE_INPUT_REQUIRED,
        text=(
            "evolution plan is ready for review - approve to start "
            "implementation, or reply with feedback to revise the plan"
        ),
        progress={"stage": "awaiting_approval", "branch": result.branch or ""},
    )
    gate.metadata.get_or_create_struct(GATE_METADATA_KEY).update(stash)
    return [_result_artifact_event(task, result), gate]


def result_events(
    task: Task,
    result: "EvolutionResult",
) -> list[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """Map a terminal EvolutionResult onto the result artifact + terminal status.

    A cancelled run maps to no events at all: cancellation only ever comes
    from the A2A cancel flow, and the executor's TaskUpdater.cancel() already
    owns the terminal CANCELED event - emitting a second terminal here would
    race it.
    """
    if result.outcome == "cancelled":
        return []

    artifact_event = _result_artifact_event(task, result)
    if result.outcome == "failed":
        terminal = failed_event(task, error=result.error or "evolution run failed")
    elif result.outcome == "no_change":
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text="no changes produced",
        )
    else:
        text = f"pushed {result.branch} @ {result.commit_sha}"
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            text = f"{text} ({pr_url})"
        terminal = status_event(task, state=TaskState.TASK_STATE_COMPLETED, text=text)
    return [artifact_event, terminal]
