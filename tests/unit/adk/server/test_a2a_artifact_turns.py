"""The artifact service across turns: what the store holds, what this turn wrote, and no database."""

from __future__ import annotations

from typing import Optional

import pytest
from a2a.types import Artifact, Part, Task
from google.genai import types

from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.core.runtime.context.conversation import ArtifactVersion, ConversationHistory
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider

APP, USER, CONTEXT = "agent", "owner", "ctx-1"


class _History(ConversationHistory):
    """A context's tasks as a turn's conversation history serves them, oldest first."""

    def __init__(self, *, task_id: str, tasks: list[Task]) -> None:
        super().__init__(context_id=CONTEXT, task_id=task_id)
        self._tasks = tasks

    async def generation(self) -> Optional[str]:
        return None

    async def get_task(self, task_id: str) -> Optional[Task]:
        return next((task for task in self._tasks if task.id == task_id), None)

    async def tasks(self, *, unfinished: bool = False) -> list[Task]:
        return list(self._tasks)

    async def artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
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
