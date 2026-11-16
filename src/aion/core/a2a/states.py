"""The A2A task states, grouped by what they say about a task.

A task ends in a terminal state, waits for its caller in an interrupt state
and is being worked on in an active one. The groups are sets of
``a2a.types.TaskState`` values, to test a task's state against and to filter
tasks by:

    from aion.core.a2a import UNFINISHED_TASK_STATES
    from aion.core.agent import conversation

    waiting = await conversation.list_tasks(states=UNFINISHED_TASK_STATES)
"""

from a2a.types import TaskState

__all__ = [
    "ACTIVE_TASK_STATES",
    "INTERRUPT_TASK_STATES",
    "TERMINAL_TASK_STATES",
    "UNFINISHED_TASK_STATES",
]


TERMINAL_TASK_STATES = frozenset({
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
})
"""The states a task ends in: it has its outcome and is never written again."""

INTERRUPT_TASK_STATES = frozenset({
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
})
"""The states a task waits in: nothing runs until its caller answers."""

ACTIVE_TASK_STATES = frozenset(
    state
    for state in TaskState.values()
    if state not in TERMINAL_TASK_STATES
    and state not in INTERRUPT_TASK_STATES
    and state != TaskState.TASK_STATE_UNSPECIFIED
)
"""The states a task is being worked on in.

Derived as the complement rather than listed, so a state added to the
protocol counts as active until it is classified as an outcome or an
interrupt. The unspecified default is left out: it names the absence of a
state, not a running task, and a store never writes it.
"""

UNFINISHED_TASK_STATES = ACTIVE_TASK_STATES | INTERRUPT_TASK_STATES
"""The states of a task without an outcome yet: being worked on, or waiting for its caller."""
