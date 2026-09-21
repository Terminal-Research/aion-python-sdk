"""A completed task survives a full server restart.

The server writes tasks to PostgreSQL when it has a ``POSTGRES_URL``. After
a restart (new OS process, same port, same config, same database), a
``tasks/get`` must return the same task with the same data -- nothing lost,
nothing mutated.

This is the scenario that distinguishes a durable deployment from one that
relies on an in-memory store no one asked for.
"""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import echo_text
from tests.scenarios.harness import ScenarioClient, ServeProcess, final_task, reply_texts


pytestmark = [pytest.mark.persistence]


@pytest.mark.command("echo")
async def test_completed_task_survives_server_restart(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """tasks/get after a restart returns the same task with the same data."""
    events = await pg_client.send("echo survive-restart")
    closing = final_task(events)

    assert closing.state == "COMPLETED"
    task_id = closing.task_id
    original_texts = reply_texts(events)
    assert echo_text("survive-restart") in original_texts

    pg_server.restart()
    async with await ScenarioClient.connect(
        pg_server.base_url, pg_server.variant.agent_id
    ) as fresh_client:
        restored = await fresh_client.get_task(task_id)

    assert restored.status.state == TaskState.TASK_STATE_COMPLETED
    assert any(
        echo_text("survive-restart") in part.text
        for message in restored.history
        for part in message.parts
    )


@pytest.mark.command("steps")
async def test_task_history_survives_server_restart(
    pg_server: ServeProcess,
    pg_client: ScenarioClient,
) -> None:
    """A multi-step task's full history is intact after a restart."""
    events = await pg_client.send("steps 3")
    closing = final_task(events)
    task_id = closing.task_id

    before = await pg_client.get_task(task_id)
    history_before = len(before.history)
    assert history_before >= 3

    pg_server.restart()
    async with await ScenarioClient.connect(
        pg_server.base_url, pg_server.variant.agent_id
    ) as fresh_client:
        after = await fresh_client.get_task(task_id)

    assert len(after.history) == history_before
