from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Optional, TypedDict
from uuid import UUID

from langgraph_api.serde import Fragment

MetadataInput = dict[str, Any] | None
MetadataValue = dict[str, Any]

RunStatus = Literal["pending", "error", "success", "timeout", "interrupted"]

ThreadStatus = Literal["idle", "busy", "interrupted", "error"]

StreamMode = Literal["values", "messages", "updates", "events", "debug", "custom"]

MultitaskStrategy = Literal["reject", "rollback", "interrupt", "enqueue"]

OnConflictBehavior = Literal["raise", "do_nothing"]

OnCompletion = Literal["delete", "keep"]

IfNotExists = Literal["create", "reject"]

All = Literal["*"]


class Config(TypedDict, total=False):
    tags: list[str]
    """
    Tags for this call and any sub-calls (eg. a Chain calling an LLM).
    You can use these to filter calls.
    """

    recursion_limit: int
    """
    Maximum number of times a call can recurse. If not provided, defaults to 25.
    """

    configurable: dict[str, Any]
    """
    Runtime values for attributes previously made configurable on this Runnable,
    or sub-Runnables, through .configurable_fields() or .configurable_alternatives().
    Check .output_schema() for a description of the attributes that have been made 
    configurable.
    """


class Checkpoint(TypedDict):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str | None
    checkpoint_map: dict[str, Any] | None


class GraphSchema(TypedDict):
    """Graph model."""

    graph_id: str
    """The ID of the graph."""
    state_schema: dict
    """The schema for the graph state."""
    config_schema: dict
    """The schema for the graph config."""


class Assistant(TypedDict):
    """Assistant model."""

    assistant_id: UUID
    """The ID of the assistant."""
    graph_id: str
    """The ID of the graph."""
    name: str
    """The name of the assistant."""
    config: Config
    """The assistant config."""
    created_at: datetime
    """The time the assistant was created."""
    updated_at: datetime
    """The last time the assistant was updated."""
    metadata: Fragment
    """The assistant metadata."""
    version: int
    """The assistant version."""


class Thread(TypedDict):
    thread_id: UUID
    """The ID of the thread."""
    created_at: datetime
    """The time the thread was created."""
    updated_at: datetime
    """The last time the thread was updated."""
    metadata: Fragment
    """The thread metadata."""
    config: Fragment
    """The thread config."""
    status: ThreadStatus
    """The status of the thread. One of 'idle', 'busy', 'interrupted', "error"."""
    values: Fragment
    """The current state of the thread."""
    interrupts: Fragment
    """The current interrupts of the thread, a map of task_id to list of interrupts."""


class ThreadTask(TypedDict):
    id: str
    name: str
    error: str | None
    interrupts: list[dict]
    checkpoint: Checkpoint | None
    state: Optional["ThreadState"]


class ThreadState(TypedDict):
    values: dict[str, Any]
    """The state values."""
    next: Sequence[str]
    """The name of the node to execute in each task for this step."""
    checkpoint: Checkpoint
    """The checkpoint keys. This object can be passed to the /threads and /runs 
    endpoints to resume execution or update state."""
    metadata: Fragment
    """Metadata for this state"""
    created_at: str | None
    """Timestamp of state creation"""
    parent_checkpoint: Checkpoint | None
    """The parent checkpoint. If missing, this is the root checkpoint."""
    tasks: Sequence[ThreadTask]
    """Tasks to execute in this step. If already attempted, may contain an error."""


class Run(TypedDict):
    run_id: UUID
    """The ID of the run."""
    thread_id: UUID
    """The ID of the thread."""
    assistant_id: UUID
    """The assistant that was used for this run."""
    created_at: datetime
    """The time the run was created."""
    updated_at: datetime
    """The last time the run was updated."""
    status: RunStatus
    """The status of the run. One of 'pending', 'error', 'success'."""
    metadata: Fragment
    """The run metadata."""
    kwargs: Fragment
    """The run kwargs."""
    multitask_strategy: MultitaskStrategy
    """Strategy to handle concurrent runs on the same thread."""


class RunSend(TypedDict):
    node: str
    input: dict[str, Any] | None


class RunCommand(TypedDict):
    goto: str | RunSend | Sequence[RunSend | str] | None
    update: dict[str, Any] | Sequence[tuple[str, Any]] | None
    resume: Any | None


class Cron(TypedDict):
    """Cron model."""

    cron_id: UUID
    """The ID of the cron."""
    thread_id: UUID | None
    """The ID of the thread."""
    end_time: datetime | None
    """The end date to stop running the cron."""
    schedule: str
    """The schedule to run, cron format."""
    created_at: datetime
    """The time the cron was created."""
    updated_at: datetime
    """The last time the cron was updated."""
    payload: Fragment
    """The run payload to use for creating new run."""


class ThreadUpdateResponse(TypedDict):
    """Response for updating a thread."""

    checkpoint: Checkpoint


class QueueStats(TypedDict):
    n_pending: int
    n_running: int
    max_age_secs: datetime | None
    med_age_secs: datetime | None
