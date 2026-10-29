"""``aion serve`` reports each error in the phase it happened in.

Focus areas:
  - An error raised once the deployment is serving stays out of the startup
    trace and is not reported as a failed start.
  - A startup failure is logged once with its traceback, whether the command
    or the handler finishes the attempt.
  - A cancellation before serving still ends the startup attempt.
"""

import asyncio
import importlib
import logging
import sys
import types
from types import SimpleNamespace

import asyncclick as click
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

import aion.core.logging
import aion.server.logging
from aion.server.logging.filters import ServerAionContextFilter

serve_module = importlib.import_module("aion.cli.commands.serve")


@pytest.fixture(autouse=True)
def tracing(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield
    provider.shutdown()


@pytest.fixture
def records(monkeypatch, caplog):
    """Log records with the trace that was current when each was emitted."""
    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            ServerAionContextFilter().filter(record)
            captured.append(record)

    monkeypatch.setattr(aion.server.logging, "setup_root_logger", lambda: None)
    monkeypatch.setattr(aion.core.logging, "set_process_role", lambda role: None)
    caplog.set_level(logging.INFO)
    handler = _Capture()
    logging.getLogger().addHandler(handler)
    yield captured
    logging.getLogger().removeHandler(handler)


def _configure(monkeypatch, load=None, run=None):
    """Stand in for the configuration reader and the serve handler."""

    class _Reader:
        def load_and_validate_config(self):
            if load is not None:
                load()
            return SimpleNamespace(agents={"agent": object()})

    class _Handler:
        async def run(self, **kwargs):
            return await run(kwargs["startup_span"])

    handlers = types.ModuleType("aion.cli.handlers.serve")
    handlers.ServeHandler = _Handler
    monkeypatch.setitem(sys.modules, "aion.cli.handlers.serve", handlers)
    monkeypatch.setattr(serve_module, "AionConfigReader", _Reader)


async def _serve():
    await serve_module.serve.callback(
        port=None, port_range_start=None, port_range_end=None, startup_timeout=30)


def _messages(records):
    return [record.getMessage() for record in records]


async def test_error_while_serving_stays_out_of_the_startup_trace(monkeypatch, records):
    async def run(startup):
        startup.finish("ready")
        raise RuntimeError("monitor failed while serving")

    _configure(monkeypatch, run=run)
    with pytest.raises(click.ClickException, match="stopped on an error"):
        await _serve()

    ready, = [record for record in records if record.getMessage() == "application.startup ready"]
    stopped, = [record for record in records if "stopped on an error" in record.getMessage()]
    assert ready.trace_id
    assert stopped.trace_id is None
    assert stopped.exc_info
    assert not any("Failed to start server" in message for message in _messages(records))


async def test_handler_startup_failure_is_logged_once(monkeypatch, records):
    async def run(startup):
        error = RuntimeError("agents did not start")
        startup.finish("failed", error)
        raise error

    _configure(monkeypatch, run=run)
    with pytest.raises(click.ClickException, match="Unable to start AION system"):
        await _serve()

    assert [record.getMessage() for record in records if record.exc_info] == [
        "application.startup failed"]


async def test_command_startup_failure_is_logged_once_in_the_startup_trace(monkeypatch, records):
    def load():
        raise ValueError("broken aion.yaml")

    _configure(monkeypatch, load=load)
    with pytest.raises(click.ClickException, match="Unable to start AION system"):
        await _serve()

    cause, = [record for record in records if record.getMessage().startswith("Failed to start server")]
    failed, = [record for record in records if record.getMessage() == "application.startup failed"]
    assert cause.trace_id and cause.trace_id == failed.trace_id
    assert [record for record in records if record.exc_info] == [failed]


async def test_cancellation_before_serving_ends_the_startup(monkeypatch, records):
    def load():
        raise asyncio.CancelledError()

    _configure(monkeypatch, load=load)
    with pytest.raises(asyncio.CancelledError):
        await _serve()

    assert "application.startup failed" in _messages(records)
