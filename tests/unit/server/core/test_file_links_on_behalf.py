"""Expired file links are issued again on behalf of the caller, through the real request path.

The request handler, the registry, ``ActiveTask`` and its consumer, the push
senders and ``StoredFileLinks`` are the real ones. Only the storage backend -
which records on whose behalf each link was asked for - and the webhook's
HTTP client stand in. The usage attribution travels as a client sends it:
the extension's URI in ``A2A-Extensions`` (``requested_extensions``) and the
carrier in the ``Aion-Usage-Attribution`` header.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from a2a.auth.user import User
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import (
    Artifact,
    GetTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.core.a2a.files import StoredFile
from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER, USAGE_ATTRIBUTION_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.files.links import StoredFileLinks
from aion.server.files.storage import FileStorageBackend, FileUploadManager, UploadReceipt
from aion.server.tasks.push_sender import AionPushNotificationSender
from aion.server.tasks.stores import InMemoryTaskStore

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
STORED_ADDRESS = "https://files.test/files/file-1/versions/version-1/content"
WEBHOOK_URL = "https://callbacks.test/push"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def protocol_extensions():
    aion_a2a_extension_registry.reset_to_default()


@pytest.fixture
def execution_scope():
    init_execution_scope()
    yield
    clear_execution_scope()


class _Alice(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


class _RecordingBackend(FileStorageBackend):
    """Issues a link to every file it is asked about, and records on whose behalf it was asked."""

    def __init__(self) -> None:
        self.asked_by: list[str | None] = []

    async def store_many(self, uploads, *, context):
        raise AssertionError("nothing is uploaded here")

    async def renew_link(self, file_id, version_id, *, context=None):
        self.asked_by.append(context.usage_attribution if context is not None else None)
        return UploadReceipt(
            uri=f"https://files.test/content?grant=new-{len(self.asked_by)}",
            file_id=file_id,
            version_id=version_id,
            access_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        )


def _stored_file_part(file_id: str = "file-1") -> Part:
    """A part as the Aion backend stores it: the version's address, which works only with authorization."""
    address = STORED_ADDRESS.replace("file-1", file_id)
    part = Part(url=address, filename=f"{file_id}.pdf", media_type="application/pdf")
    StoredFile.create(file_id, "version-1", retention_expires_at=NOW + timedelta(days=29)).write_to(part)
    return part


def _file_artifact(context: RequestContext, artifact_id: str, file_id: str = "file-1") -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=context.task_id,
        context_id=context.context_id,
        artifact=Artifact(artifact_id=artifact_id, parts=[_stored_file_part(file_id)]),
    )


class _AnsweringWithAFile(AgentExecutor):
    """A turn that answers with an artifact holding a stored file."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id = context.task_id, context.context_id
        await event_queue.enqueue_event(
            Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
        )
        await event_queue.enqueue_event(_file_artifact(context, "report"))
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


class _AnsweringTwoMessages(AgentExecutor):
    """The first message's turn answers with one file and waits; the next message's answers with another."""

    def __init__(self) -> None:
        self.first_turn_waits = asyncio.Event()
        self.release_first_turn = asyncio.Event()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await event_queue.enqueue_event(_file_artifact(context, "first-report", "file-1"))
            self.first_turn_waits.set()
            await self.release_first_turn.wait()
            return
        await event_queue.enqueue_event(_file_artifact(context, "second-report", "file-2"))
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def _call_context(carrier: str | None, *, declared: bool = True) -> ServerCallContext:
    """What a2a-sdk's call context builder makes of the request's headers."""
    headers = {AION_USAGE_ATTRIBUTION_HEADER.lower(): carrier} if carrier is not None else {}
    return ServerCallContext(
        user=_Alice(),
        state={"headers": headers},
        requested_extensions={USAGE_ATTRIBUTION_EXTENSION_URI_V1} if declared else set(),
    )


def _webhook() -> Mock:
    client = Mock()
    client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", WEBHOOK_URL)))
    return client


def _handler(
        backend: _RecordingBackend,
        webhook: Mock,
        store: InMemoryTaskStore,
        agent_executor: AgentExecutor | None = None,
) -> AionRequestHandler:
    links = StoredFileLinks(FileUploadManager(backend), now=lambda: NOW)
    configs = InMemoryPushNotificationConfigStore()
    card = Mock()
    card.capabilities.streaming = True
    return AionRequestHandler(
        agent_executor=agent_executor or _AnsweringWithAFile(),
        task_store=store,
        agent_card=card,
        push_config_store=configs,
        push_sender=AionPushNotificationSender(httpx_client=webhook, config_store=configs, file_links=links),
        file_links=links,
    )


def _urls(payload) -> list[str]:
    """Every file part address in a delivered or answered task, artifact or update."""
    if isinstance(payload, Task):
        return [part.url for artifact in payload.artifacts for part in artifact.parts if part.url]
    task = payload.get("task") or {}
    update = payload.get("artifactUpdate") or {}
    artifacts = task.get("artifacts", []) + ([update["artifact"]] if update else [])
    return [part["url"] for artifact in artifacts for part in artifact.get("parts", []) if "url" in part]


async def _until(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not condition():
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_a_turns_answer_and_pushes_ask_for_links_on_the_senders_behalf(execution_scope):
    backend, webhook = _RecordingBackend(), _webhook()
    handler = _handler(backend, webhook, InMemoryTaskStore())
    request = SendMessageRequest(
        message=Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text="the report, please")]),
        configuration=SendMessageConfiguration(task_push_notification_config=TaskPushNotificationConfig(url=WEBHOOK_URL)),
    )

    answer = await handler.on_message_send(request, _call_context("carrier-1"))
    await _until(lambda: any("task" in call.kwargs["json"] and call.kwargs["json"]["task"].get("artifacts")
                             for call in webhook.post.await_args_list))

    pushed = [url for call in webhook.post.await_args_list for url in _urls(call.kwargs["json"])]
    assert pushed and STORED_ADDRESS not in pushed
    assert _urls(answer) and STORED_ADDRESS not in _urls(answer)
    assert backend.asked_by and set(backend.asked_by) == {"carrier-1"}


def _askers(payload, backend: _RecordingBackend) -> set[str | None]:
    """On whose behalf each link in ``payload`` was issued: the backend numbers its links in the order asked."""
    return {backend.asked_by[int(url.rsplit("new-", 1)[1]) - 1] for url in _urls(payload)}


@pytest.mark.anyio
async def test_a_message_queued_into_a_running_task_has_its_turns_pushes_ask_on_its_senders_behalf(execution_scope):
    """The second message runs its turn on the task's live ``ActiveTask``; its pushes ask as its sender."""
    backend, webhook, agent = _RecordingBackend(), _webhook(), _AnsweringTwoMessages()
    handler = _handler(backend, webhook, InMemoryTaskStore(), agent)
    first = SendMessageRequest(
        message=Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text="the first report, please")]),
        configuration=SendMessageConfiguration(
            task_push_notification_config=TaskPushNotificationConfig(url=WEBHOOK_URL),
            return_immediately=True,
        ),
    )

    started = await handler.on_message_send(first, _call_context("carrier-1"))
    await agent.first_turn_waits.wait()
    active_task = await handler._active_task_registry.get(started.id)
    queued, enqueue = asyncio.Event(), active_task.enqueue_request

    async def enqueue_and_signal(request_context):
        request_id = await enqueue(request_context)
        queued.set()
        return request_id

    active_task.enqueue_request = enqueue_and_signal
    second = SendMessageRequest(
        message=Message(
            message_id="m-2",
            role=Role.ROLE_USER,
            task_id=started.id,
            context_id=started.context_id,
            parts=[Part(text="and the second one")],
        ),
    )
    answering = asyncio.create_task(handler.on_message_send(second, _call_context("carrier-2")))
    await queued.wait()
    agent.release_first_turn.set()
    answer = await answering

    def completed_tasks() -> list[dict]:
        return [
            call.kwargs["json"] for call in webhook.post.await_args_list
            if call.kwargs["json"].get("task", {}).get("status", {}).get("state") == "TASK_STATE_COMPLETED"
        ]

    await _until(lambda: bool(completed_tasks()))
    artifact_updates = {
        call.kwargs["json"]["artifactUpdate"]["artifact"]["artifactId"]: _askers(call.kwargs["json"], backend)
        for call in webhook.post.await_args_list
        if "artifactUpdate" in call.kwargs["json"]
    }
    completed = completed_tasks()
    assert answer.status.state == TaskState.TASK_STATE_COMPLETED
    assert artifact_updates == {"first-report": {"carrier-1"}, "second-report": {"carrier-2"}}
    assert len(completed) == 1 and len(_urls(completed[0])) == 2
    assert _askers(completed[0], backend) == {"carrier-2"}


async def _stored_task(store: InMemoryTaskStore) -> Task:
    task = Task(
        id="t-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(artifact_id="report", parts=[_stored_file_part()])],
    )
    await store.save(task, _call_context(None))
    return task


@pytest.mark.anyio
async def test_a_read_asks_for_links_on_behalf_of_the_attribution_it_declares():
    backend, store = _RecordingBackend(), InMemoryTaskStore()
    await _stored_task(store)
    handler = _handler(backend, _webhook(), store)

    got = await handler.on_get_task(GetTaskRequest(id="t-1"), _call_context("carrier-2"))
    listed = await handler.on_list_tasks(ListTasksRequest(), _call_context("carrier-2"))

    assert STORED_ADDRESS not in _urls(got)
    assert STORED_ADDRESS not in _urls(listed.tasks[0])
    assert backend.asked_by == ["carrier-2"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("carrier", "declared"),
    [(None, False), ("carrier-2", False), (None, True)],
    ids=["no attribution", "carrier without its declaration", "declaration without its carrier"],
)
async def test_a_read_without_an_activated_attribution_asks_as_the_daemon(carrier, declared):
    backend, store = _RecordingBackend(), InMemoryTaskStore()
    await _stored_task(store)
    handler = _handler(backend, _webhook(), store)

    got = await handler.on_get_task(GetTaskRequest(id="t-1"), _call_context(carrier, declared=declared))

    assert got is not None
    assert backend.asked_by == [None]
