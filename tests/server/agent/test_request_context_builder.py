"""Tests for AionRequestContextBuilder._find_interrupted_task."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from a2a.server.context import ServerCallContext, User
from a2a.types import Task, TaskState, TaskStatus

from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.tasks.stores.base_task_store import BaseTaskStore


def _make_task(state: TaskState) -> Task:
    return Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=state))


def _make_store(last_task: Task | None) -> BaseTaskStore:
    store = MagicMock(spec=BaseTaskStore)
    store.get_context_last_task = AsyncMock(return_value=last_task)
    return store


class _AuthenticatedUser(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "caller-123"


def _context() -> ServerCallContext:
    return ServerCallContext(user=_AuthenticatedUser())


class TestFindInterruptedTask:
    @pytest.fixture
    def builder(self):
        return AionRequestContextBuilder(task_store=_make_store(None))

    async def test_returns_none_when_no_task_exists(self):
        """No prior task for the context — should return None, not raise TypeError."""
        store = _make_store(None)
        builder = AionRequestContextBuilder(task_store=store)
        context = _context()
        result = await builder._find_interrupted_task("ctx-new", context)
        assert result is None
        store.get_context_last_task.assert_awaited_once_with(
            context_id="ctx-new",
            context=context,
        )

    async def test_returns_none_when_task_not_interrupted(self):
        """Task exists but is completed — should return None."""
        store = _make_store(_make_task(TaskState.TASK_STATE_COMPLETED))
        builder = AionRequestContextBuilder(task_store=store)
        result = await builder._find_interrupted_task(
            "ctx-1",
            _context(),
        )
        assert result is None

    async def test_returns_task_when_interrupted(self):
        """Task exists and is interrupted — should return it."""
        task = _make_task(TaskState.TASK_STATE_INPUT_REQUIRED)
        store = _make_store(task)
        builder = AionRequestContextBuilder(task_store=store)
        result = await builder._find_interrupted_task(
            "ctx-1",
            _context(),
        )
        assert result is task

    async def test_returns_none_when_no_task_store(self):
        """No task store configured — should return None."""
        builder = AionRequestContextBuilder(task_store=None)
        result = await builder._find_interrupted_task(
            "ctx-1",
            _context(),
        )
        assert result is None

    async def test_anonymous_caller_does_not_resume_a_shared_context(self):
        store = _make_store(_make_task(TaskState.TASK_STATE_INPUT_REQUIRED))
        builder = AionRequestContextBuilder(task_store=store)

        result = await builder._find_interrupted_task(
            "ctx-1",
            ServerCallContext(),
        )

        assert result is None
        store.get_context_last_task.assert_not_awaited()
