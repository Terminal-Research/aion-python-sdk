"""A2A task state groupings used for routing and lifecycle decisions."""

from a2a.server.request_handlers.default_request_handler import TERMINAL_TASK_STATES
from a2a.types import TaskState

from aion.core.a2a.enums import ArtifactId
from aion.core.a2a.metadata import EPHEMERAL_STATUS_METADATA_KEY

__all__ = [
    "ACTIVE_TASK_STATES",
    "INTERRUPT_TASK_STATES",
    "TERMINAL_TASK_STATES",
    "TRANSIENT_ARTIFACT_IDS",
    "NON_ACTIVE_TASK_STATES",
    "EPHEMERAL_STATUS_METADATA_KEY",
]


INTERRUPT_TASK_STATES = (
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
)

NON_ACTIVE_TASK_STATES = frozenset(TERMINAL_TASK_STATES) | frozenset(INTERRUPT_TASK_STATES)

# States in which a task is presented as running, so something must be
# executing it. Derived from the complement rather than listed, so a state
# added to the protocol counts as active until it is classified as an outcome
# or an interrupt — the safe default for the queries built on this set, which
# look for executions that no longer exist. The unspecified default is left
# out: it names the absence of a state, not a running task, and a store that
# serialises enums by name never writes it.
ACTIVE_TASK_STATES = frozenset(
    state
    for state in TaskState.values()
    if state not in NON_ACTIVE_TASK_STATES and state != TaskState.TASK_STATE_UNSPECIFIED
)

# Artifacts that are sent to the client for live UX only and must not be
# persisted into the durable task state.
TRANSIENT_ARTIFACT_IDS = frozenset({
    ArtifactId.STREAM_DELTA.value,
    ArtifactId.THINKING_DELTA.value,
    ArtifactId.EPHEMERAL_MESSAGE.value,
    ArtifactId.REACTION.value,
})
