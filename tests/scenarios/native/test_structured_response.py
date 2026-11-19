"""The answer ``create_agent`` gives through ``response_format`` reaches the client.

An agent built with a ``response_format`` answers with an object of its
schema, which LangChain keeps in the graph's ``structured_response``. The
server sends it as an artifact of the task named ``structured_response``:
one data part holding the object as JSON. With ``ToolStrategy`` the model
only calls the schema's tool, so the artifact is the whole answer; with
``ProviderStrategy`` the model's text is the JSON, which streams as usual,
and the artifact follows it.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from tests.scenarios.agents.langgraph_native.graph import FORECAST_DEGREES
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import STRUCTURED_RESPONSE_AGENTS, Framework
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    ScenarioClient,
    ServeVariant,
    data_of_parts,
    final_task,
    section_texts,
    stored_artifact,
)

pytestmark = [pytest.mark.native, pytest.mark.capability("structured-response")]

FORECAST = {"city": "Kyiv", "degrees": FORECAST_DEGREES}


async def _connect(framework: Framework, strategy: str) -> ScenarioClient:
    server = serve_for(STRUCTURED_RESPONSE_AGENTS[framework.name][strategy], ServeVariant())
    return await ScenarioClient.connect(server.base_url, server.variant.agent_id)


@pytest_asyncio.fixture(loop_scope="session")
async def tool_strategy(framework: Framework):
    """A client on the agent that answers through the schema's tool."""
    async with await _connect(framework, "tool") as client:
        yield client


@pytest_asyncio.fixture(loop_scope="session")
async def provider_strategy(framework: Framework):
    """A client on the agent that answers through the provider's structured output."""
    async with await _connect(framework, "provider") as client:
        yield client


def _structured_responses(task) -> list:
    return [
        value
        for artifact in task.artifacts
        if artifact.name == "structured_response"
        for value in data_of_parts(artifact.parts)
    ]


async def test_tool_strategy_answers_with_the_object(tool_strategy: ScenarioClient) -> None:
    events = await tool_strategy.send("forecast Kyiv")

    closing = final_task(events)
    assert closing.state == "COMPLETED"
    assert section_texts(events) == []
    stored = await tool_strategy.get_task(closing.task_id)
    assert _structured_responses(stored) == [FORECAST]
    assert stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID) is None


async def test_provider_strategy_streams_the_json_and_sends_the_object(provider_strategy: ScenarioClient) -> None:
    events = await provider_strategy.send("forecast Kyiv")

    closing = final_task(events)
    assert closing.state == "COMPLETED"
    assert [json.loads(text) for text in section_texts(events)] == [FORECAST]
    stored = await provider_strategy.get_task(closing.task_id)
    assert _structured_responses(stored) == [FORECAST]
    assert stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID) is not None


async def test_the_next_turn_sends_its_own_object(tool_strategy: ScenarioClient) -> None:
    first = final_task(await tool_strategy.send("forecast Kyiv"))

    closing = final_task(await tool_strategy.send("forecast Lviv", context_id=first.context_id))

    stored = await tool_strategy.get_task(closing.task_id)
    assert _structured_responses(stored) == [{"city": "Lviv", "degrees": FORECAST_DEGREES}]
