"""What ``history`` answers: the conversation of the turn, read with ``aion.core.agent.conversation``.

The read is the same for every framework - the server installs the
conversation of a turn whichever executor runs it - so both scenario agents
answer with this.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import Role, TaskState

from aion.core.a2a import answer_text, text_of
from aion.core.agent import conversation

from tests.scenarios.commands import ARTIFACT_DATA_NAME, ARTIFACT_FILE_NAME, HistoryResponse

__all__ = ["history_text"]

_ROLES = {Role.ROLE_USER: "user", Role.ROLE_AGENT: "agent"}


async def _newest_artifact(name: str) -> Optional[conversation.ArtifactVersion]:
    """The newest artifact of the conversation with this name; None without one."""
    found = await conversation.list_artifacts(name=name, limit=1)
    return found[0] if found else None


async def history_text() -> str:
    """The JSON ``history`` answers with."""
    tasks = await conversation.list_tasks()
    newest_agent = await conversation.list_messages(role=Role.ROLE_AGENT, limit=1)
    data = await _newest_artifact(ARTIFACT_DATA_NAME)
    file = await _newest_artifact(ARTIFACT_FILE_NAME)
    return HistoryResponse(
        messages=[
            [_ROLES.get(message.role, "other"), text_of(message)]
            for message in await conversation.list_messages()
        ],
        user_texts=[text_of(message) for message in await conversation.list_messages(role=Role.ROLE_USER)],
        newest_agent_text=text_of(newest_agent[0]) if newest_agent else None,
        task_ids=[task.id for task in tasks],
        completed_task_ids=[
            task.id for task in await conversation.list_tasks(states=[TaskState.TASK_STATE_COMPLETED])
        ],
        newest_task_ids=[task.id for task in await conversation.list_tasks(limit=2)],
        answers={task.id: answer_text(task) for task in tasks},
        artifact_names=[found.name for found in await conversation.list_artifacts()],
        data=data.data if data is not None else None,
        file_text=await file.read_text() if file is not None else None,
    ).model_dump_json()
