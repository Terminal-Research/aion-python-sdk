"""A native agent remembers the conversation, the framework's way.

LangGraph keeps it in the checkpoint under ``thread_id``, ADK in the session
under ``session_id``; the adapters map the A2A ``context_id`` onto both, and
file both under the request's owner. The stand-in model's ``recall`` answers
with the earlier user messages it was given, so what it says is what the
framework handed it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.scenarios.agents.native_script import RECALL_PREFIX
from tests.scenarios.harness import (
    DISTRIBUTION_EXTENSION_URI,
    ScenarioClient,
    distribution_metadata,
    final_task,
    section_texts,
)

pytestmark = pytest.mark.native


async def test_the_next_turn_sees_the_earlier_ones(client: ScenarioClient) -> None:
    first = final_task(await client.send("say hello"))
    await client.send("weather Kyiv", context_id=first.context_id)

    events = await client.send("recall", context_id=first.context_id)

    assert section_texts(events) == [RECALL_PREFIX + "say hello | weather Kyiv"]


async def test_another_context_starts_empty(client: ScenarioClient) -> None:
    """Memory is per context, not per server."""
    await client.send("say hello")

    events = await client.send("recall")

    assert section_texts(events) == [RECALL_PREFIX]


def _through(distribution_id: str) -> dict[str, Any]:
    """Send as a platform request that came through this distribution."""
    return {
        "metadata": distribution_metadata(distribution_id=distribution_id),
        "extensions": [DISTRIBUTION_EXTENSION_URI],
    }


async def test_a_distribution_does_not_split_the_conversation(client: ScenarioClient) -> None:
    """The distribution a request came through is its channel, not its owner.

    One caller through two channels on one context is one conversation: the
    checkpoint and the session belong to the caller. Separate callers are
    covered where tokens name them (the integration isolation tests); the
    scenarios' client is one anonymous session throughout.
    """
    first = final_task(await client.send("say hello", **_through("distribution-a")))

    events = await client.send("recall", context_id=first.context_id, **_through("distribution-b"))

    assert section_texts(events) == [RECALL_PREFIX + "say hello"]


@pytest.mark.capability("store")
async def test_what_a_tool_remembered_in_the_store_is_there_in_the_next_conversation(client: ScenarioClient) -> None:
    """The LangGraph store the server gives the graph is the user's, across conversations.

    The tools run synchronously, so the store answers them from the server's
    event loop, PostgreSQL included.
    """
    await client.send("remember milk")
    await client.send("remember bread")

    events = await client.send("memories")

    assert section_texts(events) == ["bread, milk"]


@pytest.mark.capability("memory-service")
async def test_what_the_agent_kept_in_memory_is_found_in_the_next_conversation(client: ScenarioClient) -> None:
    """The ADK memory service the server gives the agent is the user's, across conversations.

    The agent keeps each conversation in memory from its ``after_agent_callback``,
    and its tool searches it with ``tool_context.search_memory``, the call
    ``load_memory`` makes.
    """
    await client.send("say the password is swordfish")

    events = await client.send("search-memory password")

    (answer,) = section_texts(events)
    assert "the password is swordfish" in answer.split(" | ")
