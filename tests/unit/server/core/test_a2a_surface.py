"""The methods and routes an agent's server answers, pinned.

The JSON-RPC methods come from a2a-sdk (A2A 1.0, and A2A 0.3 through its
compatibility adapter) and from Aion's method extensions; the routes from
``AppFactory._build_app``. Either side can change them without a line of Aion
changing - an a2a-sdk release that adds or drops a method, a route helper that
mounts something else - and without this test a client would be the first to
notice. On such a change, decide what the server should answer, and update
the lists below with ``docs/development/a2a-sdk-mapping.md``.
"""

from __future__ import annotations

import pytest
# The dispatcher first: a2a-sdk's adapter module cannot be the first of the two
# imported (it and the dispatcher import each other).
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.compat.v0_3.jsonrpc_adapter import JSONRPC03Adapter

from aion.core.a2a import AION_JSONRPC_METHOD_EXTENSION_BINDINGS
from tests.support.agent_app import agent_app

A2A_1_0_METHODS = {
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "ListTasks",
    "CancelTask",
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
    "SubscribeToTask",
    "GetExtendedAgentCard",
}

A2A_0_3_METHODS = {
    "message/send",
    "message/stream",
    "tasks/get",
    "tasks/cancel",
    "tasks/pushNotificationConfig/set",
    "tasks/pushNotificationConfig/get",
    "tasks/pushNotificationConfig/list",
    "tasks/pushNotificationConfig/delete",
    "tasks/resubscribe",
    "agent/getAuthenticatedExtendedCard",
}

AION_METHODS = {"GetContexts", "GetContext", "DeleteContext"}

ROUTES = {
    ("/", ("POST",)),
    ("/.well-known/agent-card.json", ("GET",)),
    ("/.well-known/configuration.json", ("GET",)),
    ("/health/", ("GET",)),
    ("/openapi.json", ("GET", "HEAD")),
    ("/contexts:get", ("POST",)),
    ("/context:get", ("POST",)),
    ("/context:delete", ("POST",)),
}


def test_the_a2a_1_0_methods_are_a2a_sdk_s() -> None:
    assert set(JsonRpcDispatcher.METHOD_TO_MODEL) == A2A_1_0_METHODS


def test_the_a2a_0_3_methods_are_the_compatibility_adapter_s() -> None:
    assert set(JSONRPC03Adapter.METHOD_TO_MODEL) == A2A_0_3_METHODS


def test_the_aion_methods_are_the_method_extension_bindings() -> None:
    assert set(AION_JSONRPC_METHOD_EXTENSION_BINDINGS) == AION_METHODS
    assert not AION_METHODS & (A2A_1_0_METHODS | A2A_0_3_METHODS)


@pytest.mark.asyncio
async def test_the_server_mounts_exactly_these_routes() -> None:
    async with agent_app() as served:
        mounted = {
            (route.path, tuple(sorted(route.methods or ())))
            for route in served.factory.get_fastapi_app().routes
        }

    assert mounted == ROUTES
