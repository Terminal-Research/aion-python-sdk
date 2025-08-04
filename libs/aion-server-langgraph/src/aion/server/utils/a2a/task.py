from a2a.types import Task
from aion.server.types import RESUMABLE_TASK_STATUSES

__all__ = [
    "check_if_task_is_resumable",
]


def check_if_task_is_resumable(task: Task) -> bool:
    """Check if a task can be resumed based on its current state."""
    if not isinstance(task, Task):
        return False
    return task.status.state in RESUMABLE_TASK_STATUSES
