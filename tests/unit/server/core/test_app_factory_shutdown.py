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

    upload_manager = Mock()
    upload_manager.aclose = AsyncMock()

    return AppFactory(
        aion_agent=Mock(),
        db_factory=db_factory,
        agent_factory=Mock(),
        plugin_factory=plugin_factory,
        store_manager=Mock(),
        upload_manager=upload_manager,
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


async def test_storage_closes_after_everything_that_was_handed_it(factory):
    """The manager goes to the transformer and to plugins alike.

    An in-flight task can still emit artifacts, and a plugin is free to store
    something while tearing down. Closing storage before either would fail
    those uploads for no reason other than teardown order.
    """
    call_order: list[str] = []

    factory._request_handler = Mock()
    factory._request_handler.aclose = AsyncMock(
        side_effect=lambda: call_order.append("tasks")
    )
    factory.plugin_factory.is_initialized.return_value = True
    factory.plugin_factory.teardown_all = AsyncMock(
        side_effect=lambda: call_order.append("plugins")
    )
    factory.upload_manager.aclose = AsyncMock(
        side_effect=lambda: call_order.append("storage")
    )

    await factory.shutdown()

    assert call_order == ["tasks", "plugins", "storage"]


async def test_storage_closes_before_the_database(factory):
    """Both are torn down; storage is the one the plugins were still using."""
    call_order: list[str] = []

    factory.upload_manager.aclose = AsyncMock(
        side_effect=lambda: call_order.append("storage")
    )
    factory.db_factory.is_initialized = True
    factory.db_factory.cleanup = AsyncMock(
        side_effect=lambda: call_order.append("database")
    )

    await factory.shutdown()

    assert call_order == ["storage", "database"]


async def test_shutdown_closes_the_upload_manager_once(factory):
    """One owner closes the storage client.

    The manager is handed to the transformer and to plugins alike; if any of
    them closed it, it would go out from under the others.
    """
    await factory.shutdown()

    factory.upload_manager.aclose.assert_awaited_once()


async def test_shutdown_survives_a_failing_drain(factory):
    """A drain failure must not abort the remaining teardown steps.

    Shutdown runs from a ``lifespan`` ``finally`` block; letting one failure
    propagate would skip storage, plugin and database cleanup.
    """
    factory._request_handler = Mock()
    factory._request_handler.aclose = AsyncMock(side_effect=RuntimeError("boom"))

    await factory.shutdown()

    factory.upload_manager.aclose.assert_awaited_once()


async def test_shutdown_without_a_request_handler_is_a_no_op(factory):
    """Shutting down before the app is built must not raise."""
    assert factory._request_handler is None

    await factory.shutdown()
