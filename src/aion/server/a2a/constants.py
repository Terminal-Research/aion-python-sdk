"""A2A task state groupings used for routing and lifecycle decisions."""

from aion.core.a2a.enums import ArtifactId
from aion.core.a2a.states import ACTIVE_TASK_STATES, INTERRUPT_TASK_STATES, TERMINAL_TASK_STATES

__all__ = [
    "ACTIVE_TASK_STATES",
    "INTERRUPT_TASK_STATES",
    "TERMINAL_TASK_STATES",
    "NON_RESULT_ARTIFACT_IDS",
    "NON_ACTIVE_TASK_STATES",
]


# States in which nothing is executing a task: it has ended or waits for its caller.
NON_ACTIVE_TASK_STATES = TERMINAL_TASK_STATES | INTERRUPT_TASK_STATES

# Artifacts a task keeps that are not what the task produced: the agent's
# thoughts, its reactions, and the answer the server copies from the last
# message when nothing else is. A completed task without any other artifact
# gets an `aion:assistant-answer`.
NON_RESULT_ARTIFACT_IDS = frozenset({
    ArtifactId.THINKING_DELTA.value,
    ArtifactId.REACTION.value,
    ArtifactId.ASSISTANT_ANSWER.value,
})
