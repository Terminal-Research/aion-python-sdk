"""The versions of an executor's artifacts in a context: numbering at write, and finding the latest.

Each test drives the scripted executor, which numbers and looks up its
artifacts with the turn's history (``aion.core.agent.conversation``), over the
in-memory store and context reservations, the way the server wires it: a
task's events are applied to the task and saved, so the next task of the
context counts them.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_manager import append_artifact_to_task
from a2a.types import (
    Message as A2AMessage,
    Part as A2APart,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)

from aion.core.agent import conversation
from aion.server.agent.adapters import ExecutionConfig
from aion.server.tasks.admission import ContextHolder
from aion.server.tasks.conversation import StoredConversations

from tests.support.conversation import InMemoryConversations, install
from tests.support.scripted_executor.executor import ScriptedExecutor
from tests.support.scripted_executor.script import COMPLETED, Script

pytestmark = pytest.mark.usefixtures("runtime_provider")

CONTEXT_ID = "ctx-1"
SPEC = "evolution-spec"


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _caller(name: str) -> ServerCallContext:
    return ServerCallContext(user=_User(name))


def spec(text: str = "plan", **fields: Any) -> dict:
    """A whole `evolution-spec` artifact."""
    return {"kind": "artifact", "id": SPEC, "text": text, **fields}


def _message() -> A2AMessage:
    return A2AMessage(message_id="m-user", role=Role.ROLE_USER, parts=[A2APart(text="go")])


class Context:
    """One in-memory context's store and reservations, and the tasks that wrote into it."""

    def __init__(self) -> None:
        self.memory = InMemoryConversations()
        self.store = self.memory.store
        self.ledger = self.memory.artifacts
        self.executor = ScriptedExecutor()

    async def write(
            self, task_id: str, *outputs: Any, user: str = "alice", context_id: str = CONTEXT_ID
    ) -> list[Any]:
        """Run the first turn of a new task and save what it wrote, as the pipeline does."""
        await self.memory.hold(context_id, user)
        self.executor.script = Script(outputs=list(outputs))
        current = Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        request = self._request(current, user)
        install(self.memory.for_turn(request.call_context, current))
        events = [event async for event in self.executor.stream(request, _config(current))]
        await self._save(current, events, user)
        return events

    async def write_again(self, task_id: str, *outputs: Any, user: str = "alice") -> list[Any]:
        """Run a later turn of an existing task and save what it wrote."""
        self.executor.script = Script(outputs=list(outputs))
        current = await self.store.get(task_id, _caller(user))
        request = self._request(current, user)
        install(self.memory.for_turn(request.call_context, current))
        events = [event async for event in self.executor.resume(request, _config(current))]
        await self._save(current, events, user)
        return events

    def _request(self, current: Task, user: str) -> RequestContext:
        return RequestContext(
            call_context=_caller(user),
            request=SendMessageRequest(message=_message()),
            task_id=current.id,
            context_id=current.context_id,
            task=current,
        )

    async def _save(self, current: Task, events: list[Any], user: str) -> None:
        for event in events:
            if isinstance(event, TaskArtifactUpdateEvent):
                append_artifact_to_task(current, event)
        await self.store.save(current, _caller(user))

    async def found(self, task_id: str = "reader", user: str = "alice", artifact_id: str = SPEC):
        """What the lookup answers in a turn of a task read by `user`."""
        await self.write(task_id, COMPLETED, user=user)
        return await conversation.find_artifact(artifact_id)


def _config(task: Task) -> ExecutionConfig:
    return ExecutionConfig(task_id=task.id, context_id=task.context_id)


def whole(artifact_id: str) -> dict:
    """A whole artifact of another id."""
    return {"kind": "artifact", "id": artifact_id, "text": "x"}


def versions_of(events: list[Any]) -> list[str]:
    """The `version` of every artifact event, in order."""
    return [event.artifact.metadata["version"] for event in events if isinstance(event, TaskArtifactUpdateEvent)]


# -- numbering at write -----------------------------------------------------------


async def test_the_first_version_of_an_artifact_is_0() -> None:
    context = Context()

    events = await context.write("t1", spec(), COMPLETED)

    assert versions_of(events) == ["0"]


async def test_every_task_of_the_context_writes_the_next_version() -> None:
    context = Context()

    first = await context.write("t1", spec("one"), COMPLETED)
    second = await context.write("t2", spec("two"), COMPLETED)
    third = await context.write("t3", spec("three"), COMPLETED)

    assert [versions_of(events) for events in (first, second, third)] == [["0"], ["1"], ["2"]]


async def test_chunks_and_replacements_in_one_task_keep_its_number() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED)
    chunk = spec("a", last_chunk=False)
    more = spec("b", append=True, last_chunk=True)
    replacement = spec("c")

    events = await context.write("t2", chunk, more, replacement, COMPLETED)

    assert versions_of(events) == ["1", "1", "1"]


async def test_a_later_turn_of_a_task_keeps_the_number_it_wrote() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED)
    await context.write("t2", spec(), COMPLETED)
    await context.write("t3", spec(), COMPLETED)

    events = await context.write_again("t2", spec("revised"), COMPLETED)

    assert versions_of(events) == ["1"]


async def test_each_artifact_id_is_numbered_on_its_own() -> None:
    context = Context()
    await context.write("t1", spec(), whole("notes"), COMPLETED)

    events = await context.write("t2", spec(), whole("other"), COMPLETED)

    assert versions_of(events) == ["1", "0"]


async def test_a_caller_that_does_not_hold_the_context_numbers_from_zero() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED, user="bob")

    events = await context.write("t2", spec(), COMPLETED, user="alice")

    assert versions_of(events) == ["0"]


async def test_outside_a_turn_there_is_no_conversation_to_number_or_find_in() -> None:
    with pytest.raises(conversation.ConversationUnavailableError):
        await conversation.artifact_version_to_write(SPEC)
    with pytest.raises(conversation.ConversationUnavailableError):
        await conversation.find_artifact(SPEC)


# -- finding the latest version --------------------------------------------------------


async def test_the_lookup_answers_the_latest_version() -> None:
    context = Context()
    await context.write("t1", spec("one"), COMPLETED)
    await context.write("t2", spec("two"), COMPLETED)

    found = await context.found()

    assert (found.task_id, found.version) == ("t2", 1)
    assert found.artifact.artifact_id == SPEC
    assert [part.text for part in found.artifact.parts] == ["two"]
    assert found.artifact.metadata["version"] == "1"
    assert found.updated_at is not None


async def test_the_lookup_finds_nothing_in_a_context_without_it() -> None:
    context = Context()
    await context.write("t1", COMPLETED)

    assert await context.found() is None


async def test_the_lookup_sees_only_the_context_its_caller_holds() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED, user="alice")

    assert await context.found(user="bob") is None
    assert await context.ledger.versions(CONTEXT_ID, SPEC, ContextHolder.private("bob")) == []
    alices = await context.ledger.versions(CONTEXT_ID, SPEC, ContextHolder.private("alice"))
    assert [version.task_id for version in alices] == ["t1"]


async def test_a_caller_without_a_holder_finds_nothing() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED)
    nobody = StoredConversations(
        task_store=context.store,
        context_runs=context.memory.runs,
        context_artifacts=context.ledger,
        owner_resolver=lambda _: "",
    )
    install(nobody.for_turn(_caller("alice"), Task(id="reader", context_id=CONTEXT_ID)))

    assert await conversation.find_artifact(SPEC) is None
