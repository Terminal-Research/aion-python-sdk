import pytest

pytest.importorskip("a2a")
pytest.importorskip("starlette")
pytest.importorskip("pydantic")

from aion.server.langgraph.server import A2AServer
from a2a.types import AgentCard, AgentCapabilities
from a2a.server.request_handlers.request_handler import RequestHandler
from starlette.applications import Starlette
import sys
import types


class DummyHandler(RequestHandler):
    async def on_get_task(self, params, context=None):
        return None

    async def on_cancel_task(self, params, context=None):
        return None

    async def on_message_send(self, params, context=None):
        return None

    async def on_message_send_stream(self, params, context=None):
        if False:
            yield

    async def on_set_task_push_notification_config(self, params, context=None):
        return params

    async def on_get_task_push_notification_config(self, params, context=None):
        return None

    async def on_resubscribe_to_task(self, params, context=None):
        if False:
            yield


def test_build_app() -> None:
    card = AgentCard(
        name="TestAgent",
        description="desc",
        version="0.1",
        url="http://localhost",
        skills=[],
        capabilities=AgentCapabilities(),
        authentication={"schemes": []},
        defaultInputModes=["text/plain"],
        defaultOutputModes=["application/json"],
    )
    handler = DummyHandler()
    server = A2AServer(card, handler)
    app = server.build_app()
    assert isinstance(app, Starlette)


def test_build_app_mounts_mcp_proxy(monkeypatch) -> None:
    card = AgentCard(
        name="ProxyAgent",
        description="desc",
        version="0.1",
        url="http://localhost",
        skills=[],
        capabilities=AgentCapabilities(),
        authentication={"schemes": []},
        defaultInputModes=["text/plain"],
        defaultOutputModes=["application/json"],
    )

    handler = DummyHandler()
    server = A2AServer(card, handler)

    class DummyApp(Starlette):
        pass

    dummy_proxy = DummyApp()
    monkeypatch.setitem(
        sys.modules,
        "aion.mcp",
        types.SimpleNamespace(load_proxy=lambda: dummy_proxy),
    )

    app = server.build_app()
    assert app.routes[-1].path == "/mcp"

