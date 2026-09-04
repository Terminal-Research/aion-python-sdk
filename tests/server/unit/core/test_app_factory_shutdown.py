"""Tests for resource teardown ordering in ``AppFactory.shutdown``."""

from unittest.mock import AsyncMock, Mock

import pytest

from aion.server.core.app.factory import AppFactory


@pytest.fixture
def factory() -> AppFactory:
    """Builds an AppFactory whose collaborators are all inert mocks.

    Only the shutdown path is under test, so plugins and the database report
    themselves uninitialised and contribute nothing to the call order.
    """
    plugin_factory = Mock()
    plugin_factory.is_initialized.return_value = False
    db_factory = Mock()
    db_factory.is_initialized = False

    return AppFactory(
        aion_agent=Mock(),
        db_factory=db_factory,
        agent_factory=Mock(),
        plugin_factory=plugin_factory,
        store_manager=Mock(),
        upload_manager=Mock(),
    )


async def test_shutdown_drains_active_tasks(factory):
    """Shutdown must drain the request handler's active tasks.

    The SDK spawns a producer and a consumer ``asyncio.Task`` per active task.
    Without an explicit drain they outlive the server and surface as
    ``Task was destroyed but it is pending!``.
    """
    factory._request_handler = Mock()
    factory._request_handler.aclose = AsyncMock()

    await factory.shutdown()

    factory._request_handler.aclose.assert_awaited_once()


async def test_shutdown_drains_tasks_before_uploads(factory):
    """Active tasks must be drained before the resources they use are closed.

    An in-flight task can still emit artifacts, so draining uploads first would
    race against work the tasks have not finished producing.
    """
    call_order: list[str] = []

    factory._request_handler = Mock()
    factory._request_handler.aclose = AsyncMock(
        side_effect=lambda: call_order.append("tasks")
    )
    factory._executor = Mock()
    factory._executor.drain = AsyncMock(
        side_effect=lambda: call_order.append("uploads")
    )

    await factory.shutdown()

    assert call_order == ["tasks", "uploads"]


async def test_shutdown_survives_a_failing_drain(factory):
    """A drain failure must not abort the remaining teardown steps.

    Shutdown runs from a ``lifespan`` ``finally`` block; letting one failure
    propagate would skip upload, plugin and database cleanup.
    """
    factory._request_handler = Mock()
    factory._request_handler.aclose = AsyncMock(side_effect=RuntimeError("boom"))
    factory._executor = Mock()
    factory._executor.drain = AsyncMock()

    await factory.shutdown()

    factory._executor.drain.assert_awaited_once()


async def test_shutdown_without_a_request_handler_is_a_no_op(factory):
    """Shutting down before the app is built must not raise."""
    assert factory._request_handler is None

    await factory.shutdown()
