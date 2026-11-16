"""Who answers after one agent handed the conversation to another.

An ADK ``LlmAgent`` hands a conversation to a sub-agent with
``transfer_to_agent``, and that agent answers in the same turn. The next
message is not the root agent's again: ADK's ``Runner`` goes on with the
agent that answered last, with its instruction and its tools, until that
agent hands the conversation on. The server runs the agent through that
Runner, and these scenarios hold it to the Runner's choice.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.agents.adk_transfer.agent import BILLING, LOCKED, ROUTER, SUPPORT, answer
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import TRANSFER_AGENTS, Framework
from tests.scenarios.harness import ScenarioClient, ServeVariant, final_task, reply_texts

pytestmark = [pytest.mark.native, pytest.mark.capability("agent-transfer")]


@pytest_asyncio.fixture(loop_scope="session")
async def desk(framework: Framework):
    """A client on the framework's router with sub-agents, served for the session."""
    server = serve_for(TRANSFER_AGENTS[framework.name], ServeVariant())
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def test_the_agent_handed_the_conversation_answers_the_turn(desk: ScenarioClient) -> None:
    events = await desk.send("bill for May")

    assert reply_texts(events) == [answer(BILLING, "bill for May")]
    assert final_task(events).state == "COMPLETED"


async def test_the_next_message_goes_to_the_agent_that_answered_last(desk: ScenarioClient) -> None:
    """Not to the root agent: it would answer this message itself."""
    first = final_task(await desk.send("bill for May"))

    events = await desk.send("and for June", context_id=first.context_id)

    assert reply_texts(events) == [answer(BILLING, "and for June")]


async def test_a_sub_agent_hands_the_conversation_on_and_it_stays_there(desk: ScenarioClient) -> None:
    first = final_task(await desk.send("bill for May"))

    handed_on = await desk.send("to support", context_id=first.context_id)
    after = await desk.send("it does not start", context_id=first.context_id)

    assert reply_texts(handed_on) == [answer(SUPPORT, "to support")]
    assert reply_texts(after) == [answer(SUPPORT, "it does not start")]


async def test_an_agent_that_may_not_hand_back_leaves_the_next_message_to_the_root(desk: ScenarioClient) -> None:
    """``disallow_transfer_to_parent``: the agent answers its turn, and the root agent gets the next one."""
    first = await desk.send("lock the account")

    events = await desk.send("anything else", context_id=final_task(first).context_id)

    assert reply_texts(first) == [answer(LOCKED, "lock the account")]
    assert reply_texts(events) == [answer(ROUTER, "anything else")]


async def test_another_context_starts_at_the_root_agent(desk: ScenarioClient) -> None:
    await desk.send("bill for May")

    events = await desk.send("hello")

    assert reply_texts(events) == [answer(ROUTER, "hello")]
