"""The history of a turn's conversation: what ``aion.core.agent.conversation`` reads, over the in-memory stores.

A turn reads the context of its task for the holder its caller was admitted
as: every owner's task of that context, nothing of another context, and
nothing at all once the caller does not hold the context.
"""

from __future__ import annotations

import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Part, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus

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
        lambda: conversation.current_task(),
        lambda: conversation.get_task("first"),
        lambda: conversation.tasks(),
        lambda: conversation.find_artifact("spec"),
        lambda: conversation.artifact_versions("spec"),
        lambda: conversation.generation(),
        lambda: conversation.artifact_version_to_write("spec"),
    ],
    ids=["current_task", "get_task", "tasks", "find_artifact", "artifact_versions", "generation", "version_to_write"],
)
async def test_outside_a_turn_nothing_is_read(read) -> None:
    with pytest.raises(conversation.ConversationUnavailableError):
        await read()


# -- tasks -------------------------------------------------------------------------


async def test_a_turn_reads_its_own_task_and_every_owners_task_of_its_context(memory) -> None:
    turn(memory)

    assert (await conversation.current_task()).id == "running"
    assert (await conversation.get_task("waiting")).status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert await conversation.get_task("elsewhere") is None
    assert await conversation.get_task("missing") is None


async def test_the_tasks_of_the_context_come_oldest_first(memory) -> None:
    turn(memory)

    assert [found.id for found in await conversation.tasks()] == ["first", "waiting", "running"]


async def test_unfinished_tasks_are_those_without_an_outcome(memory) -> None:
    turn(memory)

    assert [found.id for found in await conversation.tasks(unfinished=True)] == ["waiting", "running"]


async def test_the_generation_of_a_held_context_is_known(memory) -> None:
    turn(memory)

    assert await conversation.generation() is not None


# -- who reads ---------------------------------------------------------------------


async def test_a_caller_that_does_not_hold_the_context_reads_nothing(memory) -> None:
    turn(memory, user="mallory")

    assert await conversation.current_task() is None
    assert await conversation.get_task("first") is None
    assert await conversation.tasks() == []
    assert await conversation.generation() is None


async def test_a_context_being_deleted_reads_as_empty(memory) -> None:
    turn(memory)
    await InMemoryContextCatalog(memory.store, memory.admission).begin_deletion(CONTEXT_ID, "alice")

    assert await conversation.tasks() == []
    assert await conversation.get_task("first") is None
    assert await conversation.generation() is None


# -- artifact versions --------------------------------------------------------------


async def test_the_version_a_task_writes_follows_the_newest_and_holds_for_the_turn(memory) -> None:
    earlier = task("earlier")
    earlier.artifacts.append(Artifact(artifact_id="spec", parts=[Part(text="v0")]))
    earlier.artifacts[0].metadata["version"] = "0"
    await memory.store.save(earlier, caller("bob"))
    turn(memory)
    history = conversation.history()
    first = TaskArtifactUpdateEvent(task_id="running", context_id=CONTEXT_ID, artifact=Artifact(artifact_id="spec"))
    second = TaskArtifactUpdateEvent(task_id="running", context_id=CONTEXT_ID, artifact=Artifact(artifact_id="spec"))

    await conversation.stamp_artifact_version(first)
    await history.stamp_artifact_version(second)

    assert (first.artifact.metadata["version"], second.artifact.metadata["version"]) == ("1", "1")
    assert (await conversation.find_artifact("spec")).task_id == "earlier"
    assert [version.task_id for version in await conversation.artifact_versions("spec")] == ["earlier"]
