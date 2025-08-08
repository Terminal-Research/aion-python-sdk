from a2a.server.request_handlers.default_request_handler import TERMINAL_TASK_STATES as A2A_TERMINAL_TASK_STATES
from a2a.types import TaskState

__all__ = [
    "INTERRUPT_TASK_STATES",
    "TERMINAL_TASK_STATES",
]

INTERRUPT_TASK_STATES = (TaskState.input_required, TaskState.auth_required)
TERMINAL_TASK_STATES = A2A_TERMINAL_TASK_STATES
