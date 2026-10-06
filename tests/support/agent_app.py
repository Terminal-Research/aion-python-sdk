"""The agent server ``AppFactory`` builds, in this process, over a scripted LangGraph agent.

The routes, the request handler, the stores, the push configs, the
middlewares and the lifespan are the ones ``AppFactory._build_app`` puts
together for ``aion serve``; nothing is assembled by hand. What is left out is what
``AppFactory.initialize`` adds around the application - migrations, plugins,
the ``AppRegistry`` routes - and the agent is built from a graph defined here
rather than discovered from ``aion.yaml``.

Requests carry tokens from ``SIGNER``, a stand-in for Aion's published key, so
the caller of a request is the ``sub`` of its token, as on a deployed server.

The agent answers by the text of the last user message:

* ``ask`` - interrupts with a question (``TASK_STATE_INPUT_REQUIRED``); the
  next message answers it;
* anything else - replies ``echo: <text>`` and completes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional
from unittest.mock import Mock, patch

import httpx
from a2a.utils import DEFAULT_RPC_URL
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

from aion.core.config.models import AgentConfig
from aion.langgraph.server.adapter import LangGraphAdapter
from aion.server.agent.aion_agent.agent import AionAgent
from aion.server.auth import JwksKeySource, TokenVerifier
from aion.server.core.app.factory import AppFactory
from aion.server.tasks.store_manager import StoreManager

from .aion_tokens import CLIENT_ID, ISSUER, SigningKey

__all__ = ["AGENT_ID", "AGENT_PORT", "SIGNER", "ServedApp", "agent_app", "user_token"]

AGENT_ID = "protocol-agent"
AGENT_PORT = 18765
"""The port the card names; nothing listens on it, requests go through ASGI."""

SIGNER = SigningKey()
"""The stand-in for Aion's published key: it signs every caller's token."""

A2A_V1 = "1.0"


def user_token(user: str = "alice") -> str:
    """An invocation token of the Aion user ``user``."""
    return SIGNER.invocation_token(user)


def _graph() -> StateGraph:
    async def answer(state: MessagesState) -> dict:
        humans = [message for message in state["messages"] if isinstance(message, HumanMessage)]
        text = humans[-1].text
        if text == "ask":
            resume = interrupt("question?")
            (reply,) = resume["messages"]
            return {"messages": [AIMessage(content=f"answered: {reply.text}")]}
        return {"messages": [AIMessage(content=f"echo: {text}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder


def _verifier() -> TokenVerifier:
    """The verifier ``build_token_verifier`` builds, its key endpoint answering with ``SIGNER``'s key."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=SIGNER.jwks()))
    keys = JwksKeySource(
        "https://api.aion.example/runtime/a2a/verification-keys",
        client=httpx.AsyncClient(transport=transport),
    )
    return TokenVerifier(keys, issuer=ISSUER, invocation_audience=CLIENT_ID)


@dataclass
class ServedApp:
    """The built application and a client that reaches it over ASGI."""

    factory: AppFactory
    client: httpx.AsyncClient

    @property
    def card(self):
        return self.factory.aion_agent.card

    async def post(
        self,
        body: Any,
        *,
        version: Optional[str] = A2A_V1,
        user: Optional[str] = "alice",
        headers: Optional[dict[str, str]] = None,
        raw: Optional[bytes] = None,
    ) -> httpx.Response:
        """POST ``body`` (or ``raw`` bytes) to the JSON-RPC endpoint as ``user``, announcing ``version``."""
        sent = dict(headers or {})
        if version is not None:
            sent.setdefault("A2A-Version", version)
        if user is not None:
            sent.setdefault("Authorization", f"Bearer {user_token(user)}")
        if raw is not None:
            sent.setdefault("Content-Type", "application/json")
            request = self.client.post(DEFAULT_RPC_URL, content=raw, headers=sent)
        else:
            request = self.client.post(DEFAULT_RPC_URL, json=body, headers=sent)
        return await asyncio.wait_for(request, timeout=30)

    async def rpc(self, method: str, params: Optional[dict[str, Any]] = None, **options: Any) -> Any:
        """One JSON-RPC call: the response object, or every event of a stream."""
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method}
        if params is not None:
            body["params"] = params
        return events_of(await self.post(body, **options))

    async def send(
        self,
        text: str,
        *,
        context_id: Optional[str] = None,
        task_id: Optional[str] = None,
        method: str = "SendMessage",
        **options: Any,
    ) -> Any:
        """``SendMessage`` (or ``SendStreamingMessage``) with one text part."""
        message: dict[str, Any] = {
            "messageId": uuid.uuid4().hex,
            "contextId": context_id or str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        return await self.rpc(method, {"message": message}, **options)

    async def send_v03(self, text: str, *, method: str = "message/send", **options: Any) -> Any:
        """A2A v0.3's ``message/send`` (or ``message/stream``) with one text part."""
        message = {
            "kind": "message",
            "messageId": uuid.uuid4().hex,
            "contextId": str(uuid.uuid4()),
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
        }
        options.setdefault("version", None)
        return await self.rpc(method, {"message": message}, **options)


def events_of(response: httpx.Response) -> Any:
    """The JSON body of a response, or the list of events of an SSE stream."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return [
            json.loads(line[len("data:"):])
            for chunk in response.text.replace("\r\n", "\n").split("\n\n")
            for line in chunk.splitlines()
            if line.startswith("data:")
        ]
    return response.json()


@asynccontextmanager
async def agent_app(database=None) -> AsyncIterator[ServedApp]:
    """The application ``AppFactory`` builds, on PostgreSQL when ``database`` is an open ``db_manager``.

    Without one the server runs on its in-memory stores, whatever the process
    has opened elsewhere.
    """
    db = database if database is not None else SimpleNamespace(is_initialized=False)
    # ``DbFactory`` itself is left out: the database is the caller's, opened and
    # migrated before this, and closed by it afterwards.
    db_factory = SimpleNamespace(db_manager=db, is_initialized=False)
    plugin_factory = Mock()
    plugin_factory.is_initialized.return_value = False
    adapter = LangGraphAdapter(db_manager=database)

    with patch("aion.server.tasks.store_manager.db_manager", db):
        agent = await AionAgent.from_adapter(
            AGENT_ID,
            AgentConfig(path="agent.py:graph", name="Protocol agent", description="Answers by the text it gets"),
            adapter,
            _graph(),
        )
        agent._is_built = True
        agent.port = AGENT_PORT
        verifier = _verifier()
        await verifier.load()
        factory = AppFactory(
            aion_agent=agent,
            db_factory=db_factory,
            agent_factory=Mock(),
            plugin_factory=plugin_factory,
            store_manager=StoreManager(),
            token_verifier=verifier,
        )
        await factory._build_app()

    app = factory.get_fastapi_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    # The application's own lifespan: startup registers the runtime context
    # provider, shutdown is ``AppFactory.shutdown``.
    async with app.router.lifespan_context(app):
        try:
            yield ServedApp(factory=factory, client=client)
        finally:
            await client.aclose()
