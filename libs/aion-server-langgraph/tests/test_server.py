from aion.server.langgraph.server import A2AServer
from a2a.types import AgentCard, AgentCapabilities
from a2a.server.request_handlers.request_handler import RequestHandler
from starlette.applications import Starlette
import pytest


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
