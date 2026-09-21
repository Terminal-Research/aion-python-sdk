"""An A2A client pointed at one agent behind a running proxy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Mapping, Optional

import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
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
from google.protobuf.struct_pb2 import Struct

from .recorder import Ev, record_stream

__all__ = ["FileAttachment", "ScenarioClient", "AGENT_CARD_PATH"]

AGENT_CARD_PATH = "/.well-known/agent-card.json"
REQUEST_TIMEOUT_SECONDS = 60.0


def _struct(payload: Mapping[str, Any]) -> Struct:
    """A protobuf Struct carrying this mapping."""
    return ParseDict(dict(payload), Struct())


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
        push_notification_url: Optional[str] = None,
        stream: bool = True,
    ) -> list[Ev]:
        """Send one message and record everything that came back."""
        request = SendMessageRequest(
            message=self.build_message(
                text, task_id=task_id, context_id=context_id, extensions=extensions, files=files
            )
        )
        if metadata:
            request.metadata.CopyFrom(_struct(metadata))
        if push_notification_url:
            request.configuration.CopyFrom(
                SendMessageConfiguration(
                    task_push_notification_config=TaskPushNotificationConfig(
                        url=push_notification_url,
                    ),
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

    async def cancel(self, task_id: str) -> Task:
        """Ask the server to cancel a task."""
        return await self._client(True).cancel_task(CancelTaskRequest(id=task_id))

    async def subscribe(self, task_id: str) -> list[Ev]:
        """Resubscribe to a task and record what the stream delivers."""
        return await record_stream(self._client(True).subscribe(SubscribeToTaskRequest(id=task_id)))

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
