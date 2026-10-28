"""Readiness follows socket binding, without tracing socket callbacks or requests."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import uvicorn
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from aion.server.opentelemetry.startup import StartupSpan
from aion.server.core.app.lifespan import AppLifespan
from aion.server.startup import StartupServer


@pytest.fixture
def startup(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield StartupSpan("agent.startup")
    provider.shutdown()


async def test_readiness_callback_follows_socket_binding_and_socket_context_is_empty(monkeypatch, startup):
    notifications = []
    callback_contexts = []

    async def bind(self, sockets=None):
        assert notifications == []
        assert not trace.get_current_span().get_span_context().is_valid
        asyncio.get_running_loop().call_soon(
            lambda: callback_contexts.append(trace.get_current_span().get_span_context())
        )
        self.started = True

    def ready():
        notifications.append(trace.get_current_span().get_span_context())

    monkeypatch.setattr(uvicorn.Server, "startup", bind)
    server = StartupServer(uvicorn.Config(Mock()), startup, "Agent", logging.getLogger(__name__), ready)
    await server.startup()
    await asyncio.sleep(0)
    assert startup.finished
    assert len(notifications) == 1
    assert notifications[0].trace_id == startup.span.get_span_context().trace_id
    assert all(not context.is_valid for context in callback_contexts)
    assert not trace.get_current_span().get_span_context().is_valid


@pytest.mark.parametrize("error", [None, OSError("bind failed"), SystemExit(1)])
async def test_failed_startup_never_notifies_ready(monkeypatch, startup, error):
    async def fail(self, sockets=None):
        if error is not None:
            raise error

    monkeypatch.setattr(uvicorn.Server, "startup", fail)
    callback = Mock()
    server = StartupServer(uvicorn.Config(Mock()), startup, "Agent", logging.getLogger(__name__), callback)
    if error is None:
        await server.startup()
    else:
        with pytest.raises(type(error)):
            await server.startup()
    callback.assert_not_called()
    assert startup.finished
    assert startup.span.status.status_code == trace.StatusCode.ERROR
    assert not trace.get_current_span().get_span_context().is_valid


async def test_lifespan_initialization_is_traced_but_maintenance_and_shutdown_are_not(startup):
    contexts = {}

    async def maintenance(name):
        contexts[name] = trace.get_current_span().get_span_context()
        await asyncio.Event().wait()

    async def shutdown():
        contexts["shutdown"] = trace.get_current_span().get_span_context()

    factory = SimpleNamespace(
        startup_context=startup.context,
        startup_callback=lambda: contexts.update(
            lifespan=trace.get_current_span().get_span_context()),
        shutdown=shutdown,
        _request_handler=SimpleNamespace(
            resume_pending_context_deletions=lambda: maintenance("deletions"),
            sweep_abandoned_tasks=lambda: maintenance("sweep"),
        ),
    )
    lifespan = AppLifespan(factory)
    async with lifespan.executor(None):
        await asyncio.sleep(0)
        assert contexts["lifespan"].trace_id == startup.span.get_span_context().trace_id
        assert not contexts["deletions"].is_valid
        assert not contexts["sweep"].is_valid
        assert not trace.get_current_span().get_span_context().is_valid
    assert not contexts["shutdown"].is_valid
    startup.finish()
