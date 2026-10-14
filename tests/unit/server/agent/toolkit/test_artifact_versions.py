"""The versions of a toolkit's artifacts in a context: numbering at write, and ``find_artifact``.

Each test drives the scripted toolkit through a ``ToolkitTaskHandler`` over the
in-memory store and context reservations, the way the server wires it: a task's
events are applied to the task and saved, so the next task of the context
counts them.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
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

from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.toolkit import (
    Artifact,
    DataPart,
    FilePart,
    TextPart,
    ToolkitArtifactEvent,
    ToolkitRequest,
    ToolkitResult,
    ToolkitState,
)
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.extensions.toolkit import ToolkitAdmission, ToolkitTaskHandler
from aion.server.tasks.admission import ContextHolder, InMemoryContextAdmission
from aion.server.tasks.context_artifacts import InMemoryContextArtifacts
from aion.server.tasks.contexts import InMemoryContextCatalog
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

from tests.support.scripted_toolkit.toolkit import Script, ScriptedToolkit

CONTEXT_ID = "ctx-1"
SPEC = "evolution-spec"
COMPLETED = ToolkitResult(state=ToolkitState.COMPLETED)


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


def spec(text: str = "plan", **fields: Any) -> ToolkitArtifactEvent:
    """A whole `evolution-spec` event."""
    return ToolkitArtifactEvent(
        artifact=Artifact(id=SPEC, name="Spec", parts=[TextPart(text=text)]), last_chunk=True, **fields
    )


class Context:
    """One in-memory context's store and reservations, and the tasks that wrote into it."""

    def __init__(self) -> None:
        self.store = InMemoryTaskStore(resolve_user_scope)
        self.admission = InMemoryContextAdmission()
        InMemoryContextCatalog(self.store, self.admission)
        self.ledger = InMemoryContextArtifacts(self.store, self.admission)
        self.toolkit = ScriptedToolkit()
        self.handler = ToolkitTaskHandler(
            self.toolkit, context_artifacts=self.ledger, owner_resolver=resolve_user_scope, secret_values=lambda: ()
        )

    async def write(
            self, task_id: str, *outputs: Any, user: str = "alice", context_id: str = CONTEXT_ID
    ) -> list[Any]:
        """Run the first turn of a new task and save what it wrote, as the pipeline does."""
        await self.admission.admit(context_id, ContextHolder.private(user), user)
        self.toolkit.script = Script(outputs=list(outputs))
        current = Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        admission = ToolkitAdmission(
            request=ToolkitRequest(message_id="m-user", text="go"), daemon_variables={}, model_service=None
        )
        events = [event async for event in self.handler.stream(self._request(current, user), admission)]
        await self._save(current, events, user)
        return events

    async def write_again(self, task_id: str, *outputs: Any, user: str = "alice") -> list[Any]:
        """Run a later turn of an existing task and save what it wrote."""
        self.toolkit.script = Script(outputs=list(outputs))
        current = await self.store.get(task_id, _caller(user))
        events = [event async for event in self.handler.resume(self._request(current, user))]
        await self._save(current, events, user)
        return events

    def _request(self, current: Task, user: str) -> RequestContext:
        message = A2AMessage(message_id="m-user", role=Role.ROLE_USER, parts=[A2APart(text="go")])
        return RequestContext(
            call_context=_caller(user),
            request=SendMessageRequest(message=message),
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
        """What `find_artifact` answers in a turn of a task read by `user`."""
        await self.write(task_id, COMPLETED, user=user)
        _, _, context = self.toolkit.turns[-1]
        return await context.find_artifact(artifact_id)


def whole(artifact_id: str) -> ToolkitArtifactEvent:
    """A whole artifact event of another id."""
    return ToolkitArtifactEvent(artifact=Artifact(id=artifact_id, parts=[TextPart(text="x")]), last_chunk=True)


def versions_of(events: list[Any]) -> list[str]:
    """The `version` of every artifact event, in order."""
    return [event.artifact.metadata["version"] for event in events if isinstance(event, TaskArtifactUpdateEvent)]


@pytest.fixture
def runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield provider
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


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
    chunk = ToolkitArtifactEvent(artifact=Artifact(id=SPEC, parts=[TextPart(text="a")]))
    more = ToolkitArtifactEvent(artifact=Artifact(id=SPEC, parts=[TextPart(text="b")]), append=True, last_chunk=True)
    replacement = ToolkitArtifactEvent(artifact=Artifact(id=SPEC, parts=[TextPart(text="c")]), last_chunk=True)
    in_result = ToolkitResult(
        state=ToolkitState.COMPLETED, artifacts=[Artifact(id=SPEC, parts=[TextPart(text="d")])]
    )

    events = await context.write("t2", chunk, more, replacement, in_result)

    assert versions_of(events) == ["1", "1", "1", "1"]


async def test_a_later_turn_of_a_task_keeps_the_number_it_wrote(runtime_provider) -> None:
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


async def test_a_handler_without_context_storage_does_not_number() -> None:
    toolkit = ScriptedToolkit(Script(outputs=[spec(), COMPLETED]))
    plain = ToolkitTaskHandler(toolkit, secret_values=lambda: ())
    current = Task(id="t1", context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    message = A2AMessage(message_id="m-user", role=Role.ROLE_USER, parts=[A2APart(text="go")])
    request = RequestContext(
        call_context=ServerCallContext(),
        request=SendMessageRequest(message=message),
        task_id="t1",
        context_id=CONTEXT_ID,
        task=current,
    )
    admission = ToolkitAdmission(
        request=ToolkitRequest(message_id="m-user", text="go"), daemon_variables={}, model_service=None
    )

    events = [event async for event in plain.stream(request, admission)]

    [artifact_event] = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert "version" not in artifact_event.artifact.metadata
    assert await toolkit.turns[0][2].find_artifact(SPEC) is None


# -- find_artifact ----------------------------------------------------------------


async def test_find_artifact_answers_the_latest_version() -> None:
    context = Context()
    await context.write("t1", spec("one"), COMPLETED)
    await context.write("t2", spec("two"), COMPLETED)

    found = await context.found()

    assert (found.task_id, found.version) == ("t2", 1)
    assert found.artifact.id == SPEC
    assert [part.text for part in found.artifact.parts] == ["two"]
    assert found.updated_at is not None


async def test_find_artifact_finds_nothing_in_a_context_without_it() -> None:
    context = Context()
    await context.write("t1", COMPLETED)

    assert await context.found() is None


async def test_find_artifact_reads_the_artifact_back_in_the_protocol_model() -> None:
    context = Context()
    artifact = Artifact(
        id=SPEC,
        name="Spec",
        description="The plan",
        parts=[
            TextPart(text="plan", metadata={"lang": "md"}),
            DataPart(data={"specPath": "spec.md"}, schema="https://example.invalid/x#Spec"),
            FilePart(url="https://files.example.invalid/spec.md", filename="spec.md", media_type="text/markdown"),
        ],
        metadata={"specPath": "spec.md", "baseSha": "abc"},
    )
    await context.write("t1", ToolkitArtifactEvent(artifact=artifact, last_chunk=True), COMPLETED)

    found = await context.found()

    assert found.version == 0
    read = found.artifact
    assert (read.id, read.name, read.description) == (SPEC, "Spec", "The plan")
    assert read.metadata == {"specPath": "spec.md", "baseSha": "abc"}
    text, data, file = read.parts
    assert (text.text, text.metadata) == ("plan", {"lang": "md"})
    assert (data.data, data.data_schema) == ({"specPath": "spec.md"}, "https://example.invalid/x#Spec")
    assert (file.url, file.filename, file.media_type) == (
        "https://files.example.invalid/spec.md",
        "spec.md",
        "text/markdown",
    )


async def test_find_artifact_does_not_return_keys_the_server_reserves() -> None:
    context = Context()
    foreign = Task(id="t1", context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    stored = foreign.artifacts.add(artifact_id=SPEC, parts=[A2APart(text="plan")])
    stored.metadata.update({"version": "4", "aion:status": "x", EVENT_EXTENSION_URI_V1: {"a": 1}, "kept": "yes"})
    await context.admission.admit(CONTEXT_ID, ContextHolder.private("alice"), "alice")
    await context.store.save(foreign, _caller("alice"))

    found = await context.found()

    assert (found.version, found.artifact.metadata) == (4, {"kept": "yes"})


async def test_find_artifact_skips_an_artifact_the_protocol_cannot_hold(caplog) -> None:
    context = Context()
    foreign = Task(id="t1", context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    foreign.artifacts.add(artifact_id=SPEC)
    await context.admission.admit(CONTEXT_ID, ContextHolder.private("alice"), "alice")
    await context.store.save(foreign, _caller("alice"))

    with caplog.at_level(logging.WARNING):
        found = await context.found()

    assert found is None
    assert "cannot be read as a toolkit artifact" in caplog.text


async def test_find_artifact_sees_only_the_context_its_caller_holds() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED, user="alice")

    assert await context.found(user="bob") is None
    assert await context.ledger.versions(CONTEXT_ID, SPEC, ContextHolder.private("bob")) == []
    alices = await context.ledger.versions(CONTEXT_ID, SPEC, ContextHolder.private("alice"))
    assert [version.task_id for version in alices] == ["t1"]


async def test_a_caller_without_a_holder_finds_nothing() -> None:
    context = Context()
    await context.write("t1", spec(), COMPLETED)
    context.handler = ToolkitTaskHandler(
        context.toolkit, context_artifacts=context.ledger, owner_resolver=lambda _: "", secret_values=lambda: ()
    )

    assert await context.found() is None
