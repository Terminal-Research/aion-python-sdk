from a2a.server.request_handlers.default_request_handler import TERMINAL_TASK_STATES
from a2a.types import TaskState

from aion.shared.types.a2a.enums import ArtifactId

__all__ = [
    "INTERRUPT_TASK_STATES",
    "TERMINAL_TASK_STATES",
    "TRANSIENT_ARTIFACT_IDS",
    "NON_ACTIVE_TASK_STATES",
]

INTERRUPT_TASK_STATES = (
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
)

NON_ACTIVE_TASK_STATES = frozenset(TERMINAL_TASK_STATES) | frozenset(INTERRUPT_TASK_STATES)

# Artifacts that are sent to the client for live UX only and must not be
# persisted into the durable task state.
TRANSIENT_ARTIFACT_IDS = frozenset({
    ArtifactId.STREAM_DELTA.value,
    ArtifactId.EPHEMERAL_MESSAGE.value,
})
