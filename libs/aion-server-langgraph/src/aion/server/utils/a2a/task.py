from a2a.types import Task
from aion.shared.types import INTERRUPT_TASK_STATES

__all__ = [
    "check_if_task_is_interrupted",
]


def check_if_task_is_interrupted(task: Task) -> bool:
    """Check if a task can be resumed based on its current state."""
    if not isinstance(task, Task):
        return False
    return task.status.state in INTERRUPT_TASK_STATES
