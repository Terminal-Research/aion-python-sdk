"""The groups of task states ``aion.core.a2a`` names."""

from __future__ import annotations

from a2a.server.agent_execution.active_task import INTERRUPTED_TASK_STATES as A2A_INTERRUPTED_TASK_STATES
from a2a.server.request_handlers.default_request_handler import TERMINAL_TASK_STATES as A2A_TERMINAL_TASK_STATES
from a2a.types import TaskState

from aion.core.a2a import (
    ACTIVE_TASK_STATES,
    INTERRUPT_TASK_STATES,
    TERMINAL_TASK_STATES,
    UNFINISHED_TASK_STATES,
)


def test_the_states_a_task_ends_and_waits_in_are_a2a_sdks() -> None:
    assert TERMINAL_TASK_STATES == frozenset(A2A_TERMINAL_TASK_STATES)
    assert INTERRUPT_TASK_STATES == frozenset(A2A_INTERRUPTED_TASK_STATES)


def test_every_state_but_the_unspecified_one_is_in_exactly_one_group() -> None:
    groups = [TERMINAL_TASK_STATES, INTERRUPT_TASK_STATES, ACTIVE_TASK_STATES]

    assert frozenset().union(*groups) == frozenset(TaskState.values()) - {TaskState.TASK_STATE_UNSPECIFIED}
    assert sum(len(group) for group in groups) == len(frozenset().union(*groups))


def test_a_task_being_worked_on_is_submitted_or_working() -> None:
    assert ACTIVE_TASK_STATES == {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}


def test_an_unfinished_task_is_being_worked_on_or_waits_for_its_caller() -> None:
    assert UNFINISHED_TASK_STATES == ACTIVE_TASK_STATES | INTERRUPT_TASK_STATES
    assert not UNFINISHED_TASK_STATES & TERMINAL_TASK_STATES
