"""The history of a turn's conversation: what ``aion.core.agent.conversation`` reads, over the in-memory stores.

A turn reads the context of its task for the holder its caller was admitted
as: every owner's task of that context, nothing of another context, and
nothing at all once the caller does not hold the context.
"""

from __future__ import annotations

import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Message, Part, Role, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus

from aion.core.a2a import UNFINISHED_TASK_STATES, files_of, text_of
from aion.core.a2a.enums import A2AMetadataKey, ArtifactId, MessageType
from aion.core.agent import conversation
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.tasks.contexts import InMemoryContextCatalog

from tests.support.conversation import InMemoryConversations, install

CONTEXT_ID = "ctx-1"


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def caller(name: str = "alice") -> ServerCallContext:
    return ServerCallContext(user=_User(name))


def task(task_id: str, state: TaskState = TaskState.TASK_STATE_COMPLETED, context_id: str = CONTEXT_ID) -> Task:
    return Task(id=task_id, context_id=context_id, status=TaskStatus(state=state))


@pytest.fixture(autouse=True)
def runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield provider
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


@pytest.fixture
async def memory() -> InMemoryConversations:
    """A context alice holds, with her task, bob's task in it, and a task of another context."""
    memory = InMemoryConversations()
    await memory.hold(CONTEXT_ID, "alice")
    await memory.store.save(task("first"), caller("alice"))
    await memory.store.save(task("waiting", TaskState.TASK_STATE_INPUT_REQUIRED), caller("bob"))
    await memory.store.save(task("running", TaskState.TASK_STATE_WORKING), caller("alice"))
    await memory.store.save(task("elsewhere", context_id="ctx-2"), caller("alice"))
    return memory


def turn(memory: InMemoryConversations, user: str = "alice", task_id: str = "running") -> None:
    """Install the history of the turn of ``task_id`` that ``user`` runs."""
    install(memory.for_turn(caller(user), task(task_id, TaskState.TASK_STATE_WORKING)))


# -- outside a turn ----------------------------------------------------------------


@pytest.mark.parametrize(
    "read",
    [
        lambda: conversation.get_current_task(),
        lambda: conversation.get_task("first"),
        lambda: conversation.list_tasks(),
        lambda: conversation.list_messages(),
        lambda: conversation.list_artifacts(),
        lambda: conversation.get_artifact("spec"),
        lambda: conversation.list_artifact_versions("spec"),
        lambda: conversation.artifact_version_to_write("spec"),
        lambda: files_of([Part(url="https://files.test/a")])[0].read(),
    ],
    ids=[
        "get_current_task", "get_task", "list_tasks", "list_messages", "list_artifacts", "get_artifact",
        "list_artifact_versions", "version_to_write", "file_read",
    ],
)
async def test_outside_a_turn_nothing_is_read(read) -> None:
    with pytest.raises(conversation.ConversationUnavailableError):
        await read()


# -- tasks -------------------------------------------------------------------------


async def test_a_turn_reads_its_own_task_and_every_owners_task_of_its_context(memory) -> None:
    turn(memory)

    assert (await conversation.get_current_task()).id == "running"
    assert (await conversation.get_task("waiting")).status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert await conversation.get_task("elsewhere") is None
    assert await conversation.get_task("missing") is None


async def test_the_tasks_of_the_context_come_oldest_first(memory) -> None:
    turn(memory)

    assert [found.id for found in await conversation.list_tasks()] == ["first", "waiting", "running"]


async def test_unfinished_tasks_are_those_without_an_outcome(memory) -> None:
    turn(memory)

    assert [found.id for found in await conversation.list_tasks(states=UNFINISHED_TASK_STATES)] == ["waiting", "running"]


async def test_tasks_are_narrowed_by_state_and_counted_from_the_newest(memory) -> None:
    turn(memory)

    waiting = await conversation.list_tasks(states=[TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_COMPLETED])
    newest = await conversation.list_tasks(limit=2)
    newest_unfinished = await conversation.list_tasks(states=UNFINISHED_TASK_STATES, limit=1)

    assert [found.id for found in waiting] == ["first", "waiting"]
    assert [found.id for found in newest] == ["waiting", "running"]
    assert [found.id for found in newest_unfinished] == ["running"]
    with pytest.raises(ValueError):
        await conversation.list_tasks(limit=-1)


async def test_a_held_context_says_when_it_was_created(memory) -> None:
    turn(memory)

    assert await conversation.current().get_created_at() is not None


# -- who reads ---------------------------------------------------------------------


async def test_a_caller_that_does_not_hold_the_context_reads_nothing(memory) -> None:
    turn(memory, user="mallory")

    assert await conversation.get_current_task() is None
    assert await conversation.get_task("first") is None
    assert await conversation.list_tasks() == []
    assert await conversation.current().get_created_at() is None


async def test_a_context_being_deleted_reads_as_empty(memory) -> None:
    turn(memory)
    await InMemoryContextCatalog(memory.store, memory.admission).begin_deletion(CONTEXT_ID, "alice")

    assert await conversation.list_tasks() == []
    assert await conversation.get_task("first") is None
    assert await conversation.current().get_created_at() is None


# -- artifact versions --------------------------------------------------------------


async def test_the_version_a_task_writes_follows_the_newest_and_holds_for_the_turn(memory) -> None:
    earlier = task("earlier")
    earlier.artifacts.append(Artifact(artifact_id="spec", parts=[Part(text="v0")]))
    earlier.artifacts[0].metadata["version"] = "0"
    await memory.store.save(earlier, caller("bob"))
    turn(memory)
    history = conversation.current()
    first = TaskArtifactUpdateEvent(task_id="running", context_id=CONTEXT_ID, artifact=Artifact(artifact_id="spec"))
    second = TaskArtifactUpdateEvent(task_id="running", context_id=CONTEXT_ID, artifact=Artifact(artifact_id="spec"))

    await conversation.stamp_artifact_version(first)
    await history.stamp_artifact_version(second)

    assert (first.artifact.metadata["version"], second.artifact.metadata["version"]) == ("1", "1")
    assert (await conversation.get_artifact("spec")).task_id == "earlier"
    assert [version.task_id for version in await conversation.list_artifact_versions("spec")] == ["earlier"]


# -- messages and artifacts ----------------------------------------------------------


def said(text: str, role: int = Role.ROLE_USER) -> Message:
    return Message(message_id=text, role=role, parts=[Part(text=text)])


@pytest.fixture
async def talk(memory) -> InMemoryConversations:
    """The held context with two more tasks that said and produced something, alice's and bob's."""
    alices = task("alices")
    alices.history.extend([said("Write a report"), said("Here it is.", Role.ROLE_AGENT)])
    event = said("values")
    event.metadata[A2AMetadataKey.MESSAGE_TYPE.value] = MessageType.LANGRAPH_VALUES.value
    alices.history.append(event)
    alices.artifacts.append(Artifact(artifact_id="report", name="report.md", parts=[Part(text="v1")]))
    alices.artifacts.append(Artifact(artifact_id=ArtifactId.STREAM_DELTA.value, parts=[Part(text="Here it is.")]))
    await memory.store.save(alices, caller("alice"))

    bobs = task("bobs", TaskState.TASK_STATE_WORKING)
    bobs.history.append(said("Add numbers"))
    bobs.status.message.CopyFrom(said("Adding.", Role.ROLE_AGENT))
    bobs.artifacts.append(Artifact(artifact_id="report", name="report.md", parts=[Part(text="v2")]))
    bobs.artifacts.append(Artifact(artifact_id="stats", name="stats", parts=[Part(text="3")]))
    await memory.store.save(bobs, caller("bob"))
    return memory


def texts(messages: list[Message]) -> list[str]:
    return [text_of(message) for message in messages]


async def test_the_messages_are_every_participants_without_the_agents_own_events(talk) -> None:
    turn(talk)

    assert texts(await conversation.list_messages()) == ["Write a report", "Here it is.", "Add numbers", "Adding."]


async def test_messages_are_narrowed_by_role_task_and_count(talk) -> None:
    turn(talk)

    assert texts(await conversation.list_messages(role=Role.ROLE_USER)) == ["Write a report", "Add numbers"]
    assert texts(await conversation.list_messages(role=Role.ROLE_AGENT, limit=1)) == ["Adding."]
    assert texts(await conversation.list_messages(task_id="alices")) == ["Write a report", "Here it is."]
    assert texts(await conversation.list_messages(limit=2, offset=1)) == ["Here it is.", "Add numbers"]
    assert await conversation.list_messages(task_id="elsewhere") == []
    with pytest.raises(ValueError):
        await conversation.list_messages(role="user")


async def test_the_artifacts_are_the_latest_version_of_each(talk) -> None:
    turn(talk)

    latest = await conversation.list_artifacts()
    every = await conversation.list_artifacts(name="report.md", all_versions=True)
    with_sdk = await conversation.list_artifacts(task_id="alices", include_sdk=True)

    assert [(found.artifact_id, found.task_id, found.text) for found in latest] == [
        ("stats", "bobs", "3"), ("report", "bobs", "v2"),
    ]
    assert [found.text for found in every] == ["v2", "v1"]
    assert {found.artifact_id for found in with_sdk} == {"report", ArtifactId.STREAM_DELTA.value}
    assert [found.task_id for found in await conversation.list_artifacts(name="stats", limit=1)] == ["bobs"]
    assert latest[0].updated_at is not None


async def test_a_caller_that_does_not_hold_the_context_reads_no_message_or_artifact(talk) -> None:
    turn(talk, user="mallory")

    assert await conversation.list_messages() == []
    assert await conversation.list_artifacts() == []
    assert await conversation.get_artifact("report") is None


class TestStoredFileLinks:
    """A turn reads a stored file through a link that works now."""

    @staticmethod
    def _conversation(links):
        from unittest.mock import Mock

        from aion.server.tasks.conversation import _StoredConversation

        return _StoredConversation(
            Mock(), Mock(), Mock(), context_id="c1", task_id="t1", holder=None, file_links=links
        )

    async def test_an_expired_link_is_replaced_by_one_issued_anew(self):
        from unittest.mock import AsyncMock, Mock

        part = Part(url="https://files.test/content?grant=stored", filename="a.pdf")
        links = Mock(link_for=AsyncMock(return_value="https://files.test/content?grant=new"))

        assert await self._conversation(links).file_link(part) == "https://files.test/content?grant=new"
        links.link_for.assert_awaited_once_with(part)

    async def test_the_parts_own_link_is_used_when_no_other_is_given(self):
        from unittest.mock import AsyncMock, Mock

        part = Part(url="https://files.test/content?grant=stored", filename="a.pdf")

        assert await self._conversation(Mock(link_for=AsyncMock(return_value=None))).file_link(part) == part.url
        assert await self._conversation(None).file_link(part) == part.url

    async def test_stored_conversations_hand_their_links_to_every_turn(self):
        from unittest.mock import Mock

        from aion.server.tasks.conversation import StoredConversations

        links = Mock()
        conversations = StoredConversations(
            task_store=Mock(),
            context_runs=Mock(),
            context_artifacts=Mock(),
            owner_resolver=lambda call_context: "owner",
            file_links=links,
        )

        turn = conversations.for_turn(None, Task(id="t1", context_id="c1"))

        assert turn._file_links is links
