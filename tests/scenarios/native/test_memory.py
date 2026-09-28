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
    reply_texts,
)

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


def _through(distribution_id: str) -> dict[str, Any]:
    """Send as a platform request that came through this distribution."""
    return {
        "metadata": distribution_metadata(distribution_id=distribution_id),
        "extensions": [DISTRIBUTION_EXTENSION_URI],
    }


async def test_memory_is_per_distribution(client: ScenarioClient) -> None:
    """Two channels of one agent on one context are two conversations.

    The checkpoint and the session belong to the distribution a request came
    through; a request without one is the anonymous caller's.
    """
    first = final_task(await client.send("say hello", **_through("distribution-a")))
    context_id = first.context_id

    others = await client.send("recall", context_id=context_id, **_through("distribution-b"))
    nobodys = await client.send("recall", context_id=context_id)
    own = await client.send("recall", context_id=context_id, **_through("distribution-a"))

    assert reply_texts(others) == [RECALL_PREFIX]
    assert reply_texts(nobodys) == [RECALL_PREFIX]
    assert reply_texts(own) == [RECALL_PREFIX + "say hello"]
