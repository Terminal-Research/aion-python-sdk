"""CLI command for tailing Aion deployment version logs."""

from __future__ import annotations

import asyncio
from threading import Event, Thread
from typing import Any, Optional

import asyncclick as click

from aion.cli.services.logs import parse_since, print_version_logs


def _run_coro_in_worker(
    coro,
    loop_ready: Event,
    state: dict[str, Any],
    error: list[BaseException],
) -> None:
    """Run a coroutine inside a worker-thread event loop."""
    loop = asyncio.new_event_loop()
    task = loop.create_task(coro)
    state["loop"] = loop
    state["task"] = task
    loop_ready.set()
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        return
    except BaseException as exc:
        error.append(exc)
    finally:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for pending_task in pending:
            pending_task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        loop.close()


def _cancel_worker(
    loop_ready: Event,
    state: dict[str, Any],
    thread: Thread,
    *,
    timeout: float = 5,
) -> None:
    """Cancel the worker-thread coroutine and wait briefly for shutdown."""
    if loop_ready.wait(timeout=1):
        loop = state.get("loop")
        task = state.get("task")
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
    thread.join(timeout=timeout)


def _run_async(coro) -> None:
    """Run an async task from Click or asyncclick command callbacks."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return

    error: list[BaseException] = []
    loop_ready = Event()
    state: dict[str, Any] = {}
    thread = Thread(
        target=_run_coro_in_worker,
        args=(coro, loop_ready, state, error),
        daemon=True,
    )
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.1)
    except KeyboardInterrupt:
        _cancel_worker(loop_ready, state, thread)
        raise

    if error:
        raise error[0]


@click.command(name="logs")
@click.option(
    "--since",
    default=None,
    help=(
        "RFC 3339/ISO-8601 lower bound for log events. "
        "Defaults to the current UTC time."
    ),
)
@click.option(
    "--properties",
    "include_properties",
    is_flag=True,
    default=False,
    help="Append structured log properties as key=value pairs.",
)
def logs(since: Optional[str], include_properties: bool) -> None:
    """Tail logs for the authenticated deployment version."""
    try:
        start_time = parse_since(since)
    except ValueError as exc:
        raise click.ClickException(
            "--since must be an RFC 3339/ISO-8601 datetime."
        ) from exc

    try:
        _run_async(
            print_version_logs(
                start_time,
                include_properties=include_properties,
            )
        )
    except KeyboardInterrupt:
        return
