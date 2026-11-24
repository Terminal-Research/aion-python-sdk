"""The executors of the pool are closed at the end of a server shutdown, all at once, within their window."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock

from tests.support.executors import FakeFrameworkExecutor, request_executor
from tests.support.scripted_executor.contract import URI
from tests.support.scripted_executor.executor import ScriptedExecutor


def _pool(framework: FakeFrameworkExecutor, extension: ScriptedExecutor):
    return request_executor(framework, extensions={URI: extension})


async def test_every_executor_of_the_pool_is_closed() -> None:
    framework, extension = FakeFrameworkExecutor(), ScriptedExecutor()
    framework.aclose, extension.aclose = AsyncMock(), AsyncMock()

    await _pool(framework, extension).close_executors(within=1.0)

    framework.aclose.assert_awaited_once()
    extension.aclose.assert_awaited_once()


async def test_an_executor_that_fails_to_close_leaves_the_others_to_close(caplog) -> None:
    framework, extension = FakeFrameworkExecutor(), ScriptedExecutor()
    framework.aclose, extension.aclose = AsyncMock(side_effect=RuntimeError("boom")), AsyncMock()

    with caplog.at_level(logging.ERROR):
        await _pool(framework, extension).close_executors(within=1.0)

    extension.aclose.assert_awaited_once()
    assert "Closing the executor FakeFrameworkExecutor failed" in caplog.text


async def test_closing_gives_up_when_its_window_is_spent(caplog) -> None:
    framework, extension = FakeFrameworkExecutor(), ScriptedExecutor()

    async def never_done() -> None:
        await asyncio.sleep(10)

    framework.aclose = never_done
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        await _pool(framework, extension).close_executors(within=0.05)

    assert time.monotonic() - started < 1.0
    assert "did not close within 0.1s" in caplog.text


async def test_an_executor_with_nothing_to_release_closes_at_once() -> None:
    assert await ScriptedExecutor().aclose() is None
