from a2a.types import TaskState

__all__ = [
    "RESUMABLE_TASK_STATUSES",
]

RESUMABLE_TASK_STATUSES = (TaskState.input_required, TaskState.auth_required)
