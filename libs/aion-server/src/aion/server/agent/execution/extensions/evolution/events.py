"""Pure mappers from toolkit progress/result DTOs onto A2A task events.

Toolkit-free at runtime: functions read WorkerState/EvolutionResult
duck-typed (phase.value, outcome, branch, ...), so this module - and its
tests - work without the optional toolkit installed. The result artifact
reuses aion-core's EvolutionResultActionPayload so the outbound shape is the
one the extension spec already defines, schema-tagged on the part the same
way inbound event parts are.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from a2a.helpers import new_data_artifact_update_event
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
from aion.core.a2a.extensions.behaviour_evolution import EvolutionResultActionPayload
from aion.core.constants.a2a import (
    BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1,
    EVENT_EXTENSION_URI_V1,
)

if TYPE_CHECKING:
    from aion.toolkits.behaviour_evolution import EvolutionResult, WorkerState

__all__ = [
    "RESULT_ARTIFACT_NAME",
    "failed_event",
    "result_events",
    "snapshot_event",
    "status_event",
]

RESULT_ARTIFACT_NAME = "evolution-result"


def status_event(
    task: Task,
    *,
    state: "TaskState.ValueType",
    text: str | None = None,
) -> TaskStatusUpdateEvent:
    """A status update for the task, with an optional agent message."""
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
    return TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=status,
    )


def snapshot_event(task: Task, snapshot: "WorkerState") -> TaskStatusUpdateEvent:
    """WORKING update carrying the run's current phase (and detail, if any)."""
    text = snapshot.phase.value
    if snapshot.detail:
        text = f"{text}: {snapshot.detail}"
    return status_event(task, state=TaskState.TASK_STATE_WORKING, text=text)


def failed_event(task: Task, *, error: str) -> TaskStatusUpdateEvent:
    """Terminal FAILED update carrying the user-facing error message."""
    return status_event(task, state=TaskState.TASK_STATE_FAILED, text=error)


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

    payload = EvolutionResultActionPayload(
        outcome=result.outcome,
        branch=result.branch,
        commit_sha=result.commit_sha,
        diff_summary=result.diff_summary,
        error=result.error,
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

    if result.outcome == "failed":
        terminal = failed_event(task, error=result.error or "evolution run failed")
    elif result.outcome == "no_change":
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text="no changes produced",
        )
    else:
        terminal = status_event(
            task,
            state=TaskState.TASK_STATE_COMPLETED,
            text=f"pushed {result.branch} @ {result.commit_sha}",
        )
    return [artifact_event, terminal]
