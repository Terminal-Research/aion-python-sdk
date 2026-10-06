"""A JSON-RPC server wired the way ``AppFactory`` wires one, for the owner isolation tests.

The request path is the real one - the given middlewares,
``AionJsonRpcDispatcher``, ``AionRequestHandler`` with
``AionRequestContextBuilder`` - over the in-memory or the PostgreSQL task
store. What isolation does not touch is left out. The agent answers by the
message text: ``done``, ``ask`` (input required) or ``hold`` (working until
released).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from unittest.mock import Mock

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import DEFAULT_RPC_URL
from a2a.server.cluster import DatabaseTaskEventStream
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.db.postgres.manager import db_manager
from aion.server.core.app.api import ContextHTTPRoutes
from aion.server.tasks.admission import InMemoryContextAdmission, PostgresContextAdmission
from aion.server.tasks.contexts import InMemoryContextCatalog, PostgresContextCatalog
from aion.server.tasks.push_notifications import PushNotificationFactory
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

from .postgres_support import stores, truncate

TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
UNSUPPORTED_OPERATION = -32004
INVALID_REQUEST = -32600
INVALID_PARAMS = -32602

A2A_V1 = "1.0"
A2A_V03 = "0.3"
"""The version a call to one of A2A v0.3's methods, such as ``tasks/get``, announces."""


class ScriptedAgent:
    """Answers by the message text: ``done``, ``ask`` (input required) or ``hold``."""

    supports_context_state_deletion = True

    def __init__(self) -> None:
        self.runs: list[tuple[str, str, str]] = []
        self.release = asyncio.Event()
        self.cancels = 0
        self.deleted_state: list[tuple[str, Optional[str], Optional[tuple[str, str]]]] = []
        # The request handler reaches the running agent as `agent_executor.agent`.
        self.agent = self

    async def delete_context_state(self, context_id, *, owner_scope, gateway) -> None:
        self.deleted_state.append((context_id, owner_scope, gateway))

    async def execute(self, context, event_queue) -> None:
        text = context.get_user_input()
        self.runs.append((context.task_id, context.context_id, text))
        state = {
            "done": TaskState.TASK_STATE_COMPLETED,
            "ask": TaskState.TASK_STATE_INPUT_REQUIRED,
            "hold": TaskState.TASK_STATE_WORKING,
        }[text]
        # The first event carries the message that started the turn, as the
        # SDK's executors announce a task, so its history holds the turn.
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=state),
                history=[context.message] if context.message else [],
            )
        )
        if state == TaskState.TASK_STATE_WORKING:
            await self.release.wait()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

    async def cancel(self, context, event_queue) -> None:
        self.cancels += 1
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )


class JsonRpcServer:
    """One server behind ``middleware``, outermost first, on the ``kind`` of store."""

    def __init__(self, kind: str, middleware: list[Middleware]) -> None:
        if kind == "memory":
            self.store = InMemoryTaskStore()
            handler_store, event_stream = self.store, None
            admission = InMemoryContextAdmission()
            catalog = InMemoryContextCatalog(self.store, admission)
        else:
            # Built the way StoreManager builds it: a2a-sdk's cluster mode.
            self.store, handler_store = stores("test-agent")
            event_stream = DatabaseTaskEventStream(
                db_manager.get_engine(), create_table=False, poll_interval_s=0.05
            )
            admission = PostgresContextAdmission(self.store.agent_id, db_manager)
            catalog = PostgresContextCatalog(self.store.agent_id, db_manager)
        self.admission = admission
        self.agent = ScriptedAgent()
        card = Mock()
        card.capabilities.streaming = True
        card.capabilities.push_notifications = True
        # The push config store AppFactory builds, in memory or in the database.
        # No sender: these tests are about who reaches a config, not delivery.
        self.push_configs, _ = PushNotificationFactory.create(db_manager if kind == "postgres" else None)
        self.handler = AionRequestHandler(
            agent_executor=self.agent,
            task_store=handler_store,
            event_stream=event_stream,
            agent_card=card,
            admission=admission,
            context_catalog=catalog,
            push_config_store=self.push_configs,
            request_context_builder=AionRequestContextBuilder(
                task_store=self.store, auto_discover_interrupted_task=True
            ),
        )
        dispatcher = AionJsonRpcDispatcher(request_handler=self.handler, enable_v0_3_compat=True)
        app = Starlette(
            routes=[
                Route(DEFAULT_RPC_URL, endpoint=dispatcher.handle_requests, methods=["POST"]),
                *ContextHTTPRoutes(self.handler, base_url=DEFAULT_RPC_URL).routes(),
            ],
            middleware=middleware,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        headers: Optional[dict[str, str]] = None,
        version: str = A2A_V1,
    ) -> Any:
        """One JSON-RPC call: the response object, or every event of a stream."""
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        response = await asyncio.wait_for(
            self.client.post(
                DEFAULT_RPC_URL, json=body, headers={"A2A-Version": version, **(headers or {})}
            ),
            timeout=20,
        )
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return [
                json.loads(line[len("data:"):])
                for chunk in response.text.split("\n\n")
                for line in chunk.splitlines()
                if line.startswith("data:")
            ]
        return response.json()

    async def send(
        self,
        text: str,
        context_id: str,
        task_id: Optional[str] = None,
        *,
        metadata: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **config: Any,
    ) -> Any:
        """``SendMessage``, with ``metadata`` as its ``params.metadata`` and ``config`` as its configuration."""
        message = {
            "messageId": uuid.uuid4().hex,
            "contextId": context_id,
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        params: dict[str, Any] = {"message": message}
        if config:
            params["configuration"] = config
        if metadata is not None:
            params["metadata"] = metadata
        return await self.rpc("SendMessage", params, headers=headers)

    async def write(self, state: TaskState, context: ServerCallContext | None) -> str:
        """Create a task from Python, beside the server, as an integrator's code would."""
        task_id = str(uuid.uuid4())
        await self.store.save(
            Task(id=task_id, context_id=str(uuid.uuid4()), status=TaskStatus(state=state)), context
        )
        return task_id

    async def aclose(self) -> None:
        self.agent.release.set()
        await self.client.aclose()
        await self.handler._active_task_registry.aclose()


@asynccontextmanager
async def serving(kind: str, database, middleware: list[Middleware]) -> AsyncIterator[JsonRpcServer]:
    """A server for one test; the PostgreSQL kind skips without a test database."""
    if kind == "postgres":
        if database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    server = JsonRpcServer(kind, middleware)
    try:
        yield server
    finally:
        await server.aclose()


def task_of(response: dict) -> dict:
    assert "error" not in response, response
    return response["result"]["task"]


def error_of(response: dict | list) -> int:
    """The error code of a call; a stream refused before it starts is a plain response."""
    if isinstance(response, list):
        assert len(response) == 1, response
        [response] = response
    assert "result" not in response, response
    return response["error"]["code"]


def state_of(task: dict) -> str:
    return task["status"]["state"]
