"""An A2A client pointed at one agent behind a running proxy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Mapping, Optional

import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.compat.v0_3 import conversions
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.jsonrpc_transport import CompatJsonRpcTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    AuthenticationInfo,
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct, Value
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from .recorder import Ev, record_stream

__all__ = [
    "DataAttachment",
    "FileAttachment",
    "PushAuth",
    "ScenarioClient",
    "AGENT_CARD_PATH",
]

AGENT_CARD_PATH = "/.well-known/agent-card.json"
REQUEST_TIMEOUT_SECONDS = 60.0

A2A_VERSION_HEADER = "a2a-version"
A2A_VERSION = "1.0"
"""Protocol version the typed client announces, and so does ``rpc``."""


def _struct(payload: Mapping[str, Any]) -> Struct:
    """A protobuf Struct carrying this mapping."""
    return ParseDict(dict(payload), Struct())


def _value(payload: Mapping[str, Any]) -> Value:
    """A protobuf Value carrying this mapping, which is what ``Part.data`` is."""
    return ParseDict(dict(payload), Value())


@dataclass(frozen=True)
class FileAttachment:
    """A file a scenario sends inline, as ``Part(raw=...)``.

    Attributes:
        name: File name presented with the content.
        media_type: MIME type of the content.
        content: The bytes themselves.
    """

    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class DataAttachment:
    """A structured part a scenario sends, as ``Part(data=...)`` with metadata.

    The shape an event payload travels in: the data is the payload, and the
    metadata names the schema it was written to, which is what the server
    dispatches on.

    Attributes:
        data: The payload object itself.
        metadata: Part metadata, keyed by extension URI.
    """

    data: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PushAuth:
    """What the push callback expects the server to present.

    The A2A push configuration carries two independent credentials, and this
    is both of them: ``scheme``/``credentials`` become the ``Authorization``
    header, ``token`` becomes ``X-A2A-Notification-Token``. An empty field is
    one the configuration does not declare.

    Attributes:
        scheme: Authorization scheme, e.g. ``Bearer``.
        credentials: The credentials that follow the scheme.
        token: The opaque notification token.
    """

    scheme: str = ""
    credentials: str = ""
    token: str = ""


def _push_config(url: str, auth: Optional[PushAuth]) -> TaskPushNotificationConfig:
    """The push configuration a scenario registers with a message."""
    config = TaskPushNotificationConfig(url=url)
    if auth is None:
        return config
    if auth.token:
        config.token = auth.token
    if auth.scheme or auth.credentials:
        config.authentication.CopyFrom(
            AuthenticationInfo(scheme=auth.scheme, credentials=auth.credentials)
        )
    return config


def _pin_card_to(card: AgentCard, url: str) -> AgentCard:
    """Point every interface of a card at one URL.

    An agent card names the port the agent itself listens on. Through the
    proxy that address is reachable, but talking to it would bypass the
    entry point a deployment actually exposes, so the scenarios rewrite the
    card to the proxy route they fetched it from.
    """
    pinned = AgentCard()
    pinned.CopyFrom(card)
    for interface in pinned.supported_interfaces:
        interface.url = url
    return pinned


class _TaskReaderV03(CompatJsonRpcTransport):
    """a2a-sdk's v0.3 JSON-RPC transport, reading a task as the caller its metadata names.

    The transport's own ``get_task`` takes A2A v1's ``GetTaskRequest``, which
    has no metadata field, so it cannot say who reads. This sends the same
    ``tasks/get`` with ``TaskQueryParams.metadata`` set and otherwise goes the
    transport's way: its request conversion, its HTTP request and version
    header, its error mapping, and its conversion back to a v1 ``Task``.
    """

    async def get_task_as(self, request: GetTaskRequest, metadata: Mapping[str, Any]) -> Task:
        compat = conversions.to_compat_get_task_request(request, request_id=0)
        params = compat.params.model_copy(update={"metadata": dict(metadata)})
        rpc_request = JSONRPC20Request(
            method="tasks/get",
            params=params.model_dump(by_alias=True, exclude_none=True, mode="json"),
            _id=str(uuid.uuid4()),
        )
        response = JSONRPC20Response(**await self._send_request(dict(rpc_request.data)))
        if response.error:
            raise self._create_jsonrpc_error(response.error)
        return conversions.to_core_task(types_v03.Task.model_validate(response.result))


class ScenarioClient:
    """Talks to one agent and hands back recorded events.

    Built around two A2A clients over the same HTTP connection: a streaming
    one, which is how a deployment is normally driven, and a unary one for
    the scenarios that assert on the single Task a non-streaming send
    returns.
    """

    def __init__(self, base_url: str, agent_id: str, card: AgentCard, http_client: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.card = card
        self._http = http_client
        self._clients: dict[bool, Client] = {}

    @property
    def agent_url(self) -> str:
        """Proxy route of this agent."""
        return f"{self.base_url}/agents/{self.agent_id}"

    @classmethod
    async def connect(cls, base_url: str, agent_id: str) -> "ScenarioClient":
        """Fetch the agent card through the proxy and build the clients."""
        http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        agent_url = f"{base_url.rstrip('/')}/agents/{agent_id}"
        try:
            response = await http_client.get(f"{agent_url}{AGENT_CARD_PATH}")
            response.raise_for_status()
            card = _pin_card_to(parse_agent_card(response.json()), agent_url)
        except Exception:
            await http_client.aclose()
            raise
        return cls(base_url, agent_id, card, http_client)

    def _client(self, streaming: bool) -> Client:
        """The A2A client for this transport mode, built once."""
        if streaming not in self._clients:
            factory = ClientFactory(ClientConfig(streaming=streaming, httpx_client=self._http))
            self._clients[streaming] = factory.create(self.card)
        return self._clients[streaming]

    @staticmethod
    def _call_context(extensions: Iterable[str]) -> Optional[ClientCallContext]:
        """Ask for these extensions on the wire, via the A2A-Extensions header."""
        uris = list(extensions)
        if not uris:
            return None
        return ClientCallContext(
            service_parameters=ServiceParametersFactory.create([with_a2a_extensions(uris)])
        )

    def build_message(
        self,
        text: str,
        *,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        extensions: Iterable[str] = (),
        files: Iterable[FileAttachment] = (),
        data: Iterable[DataAttachment] = (),
        message_metadata: Optional[Mapping[str, Any]] = None,
    ) -> Message:
        """The user message a scenario sends: its text, then any attachments."""
        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=text)],
        )
        for attachment in files:
            message.parts.append(
                Part(
                    raw=attachment.content,
                    media_type=attachment.media_type,
                    filename=attachment.name,
                )
            )
        for structured in data:
            part = Part(data=_value(structured.data))
            if structured.metadata:
                part.metadata.CopyFrom(_struct(structured.metadata))
            message.parts.append(part)
        if message_metadata:
            message.metadata.CopyFrom(_struct(message_metadata))
        if task_id:
            message.task_id = task_id
        if context_id:
            message.context_id = context_id
        for uri in extensions:
            message.extensions.append(uri)
        return message

    async def send(
        self,
        text: str,
        *,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        extensions: Iterable[str] = (),
        files: Iterable[FileAttachment] = (),
        data: Iterable[DataAttachment] = (),
        message_metadata: Optional[Mapping[str, Any]] = None,
        push_notification_url: Optional[str] = None,
        push_auth: Optional[PushAuth] = None,
        stream: bool = True,
    ) -> list[Ev]:
        """Send one message and record everything that came back."""
        request = SendMessageRequest(
            message=self.build_message(
                text,
                task_id=task_id,
                context_id=context_id,
                extensions=extensions,
                files=files,
                data=data,
                message_metadata=message_metadata,
            )
        )
        if metadata:
            request.metadata.CopyFrom(_struct(metadata))
        if push_notification_url:
            request.configuration.CopyFrom(
                SendMessageConfiguration(
                    task_push_notification_config=_push_config(push_notification_url, push_auth),
                )
            )

        client = self._client(stream)
        return await record_stream(
            client.send_message(request, context=self._call_context(extensions))
        )

    async def get_task(self, task_id: str, *, history_length: Optional[int] = None) -> Task:
        """Read a task back from the server."""
        request = GetTaskRequest(id=task_id)
        if history_length is not None:
            request.history_length = history_length
        return await self._client(True).get_task(request)

    async def get_task_v03(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any],
        history_length: Optional[int] = None,
    ) -> Task:
        """Read a task back over A2A v0.3's ``tasks/get``, as the caller ``metadata`` names.

        A2A v1's ``GetTask`` has no metadata field, so it always reads as the
        anonymous caller. A task a distribution sent is that distribution's,
        and is read back this way; the answer is the v1 ``Task`` every other
        read returns.
        """
        request = GetTaskRequest(id=task_id)
        if history_length is not None:
            request.history_length = history_length
        reader = _TaskReaderV03(httpx_client=self._http, agent_card=self.card, url=self.agent_url)
        return await reader.get_task_as(request, metadata)

    async def tasks_in_context(self, context_id: str) -> list[Task]:
        """Every task the server holds under one context.

        How a scenario tells "the request was refused" from "the request was
        refused after a task had already been opened for it": the refusal is
        what the caller sees either way, and this is what the server kept.
        """
        response = await self._client(True).list_tasks(ListTasksRequest(context_id=context_id))
        return list(response.tasks)

    async def cancel(self, task_id: str) -> Task:
        """Ask the server to cancel a task."""
        return await self._client(True).cancel_task(CancelTaskRequest(id=task_id))

    async def subscribe(self, task_id: str) -> list[Ev]:
        """Resubscribe to a task and record what the stream delivers.

        Reads the subscription to its end, so it answers for a task whose
        stream the server closes on its own - a settled task it replays, or a
        running one that finishes. A task that is waiting for input on a
        single-process deployment keeps the subscription open until something
        continues it, and that one is driven with ``subscribe_stream``.
        """
        return await record_stream(self.subscribe_stream(task_id))

    def subscribe_stream(self, task_id: str) -> AsyncIterator:
        """Raw resubscribe, for scenarios that read events as they arrive."""
        return self._client(True).subscribe(SubscribeToTaskRequest(id=task_id))

    async def rpc(self, method: str, params: Mapping[str, Any]) -> dict:
        """Call one JSON-RPC method by hand and return the body as it arrived.

        The typed client is the right instrument almost everywhere, and it is
        what every other method here uses. It has one blind spot: a JSON-RPC
        error reaches the caller as an ``A2AClientError`` carrying a rendered
        message, so the error object's ``data`` - where a server puts what it
        knows about the refusal - is gone by the time a scenario could read
        it. This posts to the same public endpoint, in the same envelope the
        client uses, and hands back the parsed response instead.

        Args:
            method: The A2A method name as the wire spells it, e.g.
                ``SubscribeToTask``.
            params: The method's parameters, in protobuf JSON form.

        Returns:
            The decoded JSON-RPC response - ``result`` or ``error``.
        """
        response = await self._http.post(
            self.agent_url,
            json={
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": method,
                "params": dict(params),
            },
            headers={A2A_VERSION_HEADER: A2A_VERSION},
        )
        response.raise_for_status()
        return response.json()

    def stream(self, text: str, **kwargs: Any) -> AsyncIterator:
        """Raw streaming send, for scenarios that read events as they arrive."""
        request = SendMessageRequest(message=self.build_message(text, **kwargs))
        return self._client(True).send_message(request)

    async def close(self) -> None:
        """Close both A2A clients and the HTTP connection."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        await self._http.aclose()

    async def __aenter__(self) -> "ScenarioClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
