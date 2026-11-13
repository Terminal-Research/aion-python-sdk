"""The artifact service across turns: what the store holds, what this turn wrote, and no database.

What a turn wrote is held in memory until the turn ends; the turn's task
answers for it from then on.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional
from unittest.mock import Mock

import pytest
from a2a.types import Artifact, Message, Part, Role, Task, TaskArtifactUpdateEvent
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai import types

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.execution.adk_executor import ADKExecutor
from aion.core.config.models import AgentConfig
from aion.core.runtime.context.conversation import ArtifactVersion, Conversation
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.adapters import ExecutionConfig, StateScope
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider

APP, USER, CONTEXT = "agent", "owner", "ctx-1"


class _History(Conversation):
    """A context's tasks as a turn's conversation history serves them, oldest first."""

    def __init__(self, *, task_id: str, tasks: list[Task]) -> None:
        super().__init__(context_id=CONTEXT, task_id=task_id)
        self._tasks = tasks

    async def get_created_at(self):
        return None

    async def get_task(self, task_id: str) -> Optional[Task]:
        return next((task for task in self._tasks if task.id == task_id), None)

    async def list_tasks(self, *, states=None, limit=None) -> list[Task]:
        return list(self._tasks)

    async def list_artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        return []


def _task(task_id: str, *texts: str, name: str = "report") -> Task:
    return Task(
        id=task_id,
        context_id=CONTEXT,
        artifacts=[Artifact(artifact_id=f"{task_id}-{i}", name=name, parts=[Part(text=text)]) for i, text in enumerate(texts)],
    )


@pytest.fixture
def turn():
    """Install a turn whose conversation history serves the given tasks."""

    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())

    def install(task_id: str, *tasks: Task) -> None:
        AionRuntimeContextRegistry.set_current_context(
            AionRuntimeContext(conversation=_History(task_id=task_id, tasks=list(tasks)))
        )

    yield install
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


async def _load(service: A2AArtifactService, name: str = "report") -> Optional[str]:
    part = await service.load_artifact(app_name=APP, user_id=USER, filename=name, session_id=CONTEXT)
    return part.text if part is not None else None


async def test_without_a_database_the_tasks_artifacts_are_found_by_name(turn) -> None:
    turn("t-2", _task("t-1", "first", "second"))
    service = A2AArtifactService()

    assert await _load(service) == "second"
    assert await service.list_artifact_keys(app_name=APP, user_id=USER, session_id=CONTEXT) == ["report"]


async def test_a_version_saved_in_an_earlier_turn_yields_to_a_newer_one_in_the_store(turn) -> None:
    turn("t-1")
    service = A2AArtifactService()
    await service.save_artifact(
        app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, artifact=types.Part(text="saved in t-1")
    )

    turn("t-2", _task("t-1", "saved in t-1"), _task("t-2", "published in t-2"))

    assert await _load(service) == "published in t-2"


async def test_what_this_turn_wrote_is_the_latest_before_its_task_is_stored(turn) -> None:
    turn("t-2", _task("t-1", "from t-1"))
    service = A2AArtifactService()

    service.remember_published(
        app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, part=types.Part(text="published now")
    )

    assert await _load(service) == "published now"

    await service.save_artifact(
        app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, artifact=types.Part(text="saved after")
    )

    assert await _load(service) == "saved after"


async def test_a_published_name_is_listed_in_the_turn(turn) -> None:
    turn("t-1")
    service = A2AArtifactService()

    service.remember_published(
        app_name=APP, user_id=USER, filename="draft", session_id=CONTEXT, part=types.Part(text="x")
    )

    assert await service.list_artifact_keys(app_name=APP, user_id=USER, session_id=CONTEXT) == ["draft"]


async def _keys(service: A2AArtifactService) -> list[str]:
    return await service.list_artifact_keys(app_name=APP, user_id=USER, session_id=CONTEXT)


def _forget(service: A2AArtifactService, task_id: str) -> None:
    service.forget_turn(app_name=APP, user_id=USER, session_id=CONTEXT, task_id=task_id)


async def test_what_a_turn_wrote_leaves_memory_when_the_turn_ends(turn) -> None:
    turn("t-1")
    service = A2AArtifactService()
    await service.save_artifact(
        app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, artifact=types.Part(text="saved")
    )
    service.remember_published(
        app_name=APP, user_id=USER, filename="draft", session_id=CONTEXT, part=types.Part(text="published")
    )

    _forget(service, "t-1")
    turn("t-2")

    assert await _keys(service) == []
    assert await _load(service) is None


async def test_the_task_answers_once_the_turn_has_ended(turn) -> None:
    turn("t-1")
    service = A2AArtifactService()
    await service.save_artifact(
        app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, artifact=types.Part(text="Q3 report")
    )

    _forget(service, "t-1")
    turn("t-2", _task("t-1", "Q3 report"))

    assert await _load(service) == "Q3 report"
    assert await _keys(service) == ["report"]


async def test_the_artifacts_of_another_task_stay(turn) -> None:
    service = A2AArtifactService()
    for task_id, name in (("t-1", "report"), ("t-2", "draft")):
        turn(task_id)
        await service.save_artifact(
            app_name=APP, user_id=USER, filename=name, session_id=CONTEXT, artifact=types.Part(text=task_id)
        )

    _forget(service, "t-1")
    turn("t-3")

    assert await _keys(service) == ["draft"]
    assert await _load(service, "draft") == "t-2"


async def test_a_name_another_task_also_wrote_stays_until_eviction(turn) -> None:
    service = A2AArtifactService()
    for task_id in ("t-1", "t-2"):
        turn(task_id)
        await service.save_artifact(
            app_name=APP, user_id=USER, filename="report", session_id=CONTEXT, artifact=types.Part(text=task_id)
        )

    _forget(service, "t-1")
    turn("t-3")

    assert await _load(service) == "t-2"


async def test_a_user_scoped_artifact_stays_until_eviction(turn) -> None:
    """Other sessions of the user find it in memory alone."""
    turn("t-1")
    service = A2AArtifactService()
    await service.save_artifact(
        app_name=APP, user_id=USER, filename="user:profile", session_id=CONTEXT, artifact=types.Part(text="profile")
    )

    _forget(service, "t-1")
    turn("t-2")

    assert await _load(service, "user:profile") == "profile"


class _Reporter(BaseAgent):
    """Saves a report as a tool does: through the artifact service, named in the event's artifact delta."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        version = await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename="report",
            artifact=types.Part(text="Q3 report"),
        )
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text="Saved.")]),
            actions=EventActions(artifact_delta={"report": version}),
        )


async def test_a_turn_of_the_executor_leaves_its_artifacts_to_the_task(turn) -> None:
    """The artifact reaches the client from memory during the turn; memory holds nothing once it has ended."""
    service = A2AArtifactService()
    executor = ADKExecutor(
        agent=_Reporter(name="reporter"),
        config=AgentConfig(path="m:agent"),
        session_service=InMemorySessionService(),
        artifact_service=service,
    )
    config = ExecutionConfig(task_id="t-1", context_id=CONTEXT, state_scope=StateScope(agent_id=APP, owner_scope=USER))
    request = Mock(
        task_id="t-1",
        context_id=CONTEXT,
        message=Message(message_id="m-1", role=Role.ROLE_USER, parts=[Part(text="Make a report")]),
    )
    turn("t-1")

    events = [event async for event in executor.stream(request, config)]

    artifacts = [event.artifact for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert [(artifact.name, artifact.parts[0].text) for artifact in artifacts] == [("report", "Q3 report")]
    turn("t-2")
    assert await _keys(service) == []
