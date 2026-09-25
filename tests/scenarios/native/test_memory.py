"""A native agent remembers the conversation, the framework's way.

LangGraph keeps it in the checkpoint under ``thread_id``, ADK in the session
under ``session_id``; the adapters map the A2A ``context_id`` onto both. The
stand-in model's ``recall`` answers with the earlier user messages it was
given, so what it says is what the framework handed it.
"""

from __future__ import annotations

import pytest

from tests.scenarios.agents.native_script import RECALL_PREFIX
from tests.scenarios.harness import ScenarioClient, final_task, reply_texts

pytestmark = pytest.mark.native


async def test_the_next_turn_sees_the_earlier_ones(client: ScenarioClient) -> None:
    first = final_task(await client.send("say hello"))
    await client.send("weather Kyiv", context_id=first.context_id)

    events = await client.send("recall", context_id=first.context_id)

    assert reply_texts(events) == [RECALL_PREFIX + "say hello | weather Kyiv"]


async def test_another_context_starts_empty(client: ScenarioClient) -> None:
    """Memory is per context, not per server."""
    await client.send("say hello")

    events = await client.send("recall")

    assert reply_texts(events) == [RECALL_PREFIX]
