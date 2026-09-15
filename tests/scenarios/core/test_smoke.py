"""Does a deployment of this framework answer at all."""

from __future__ import annotations

import httpx
import pytest

from tests.scenarios.commands import help_text
from tests.scenarios.harness import ScenarioClient, ServeProcess

pytestmark = pytest.mark.smoke


def test_proxy_reports_every_agent_healthy(server: ServeProcess) -> None:
    """The proxy's system health names the agent and calls it healthy."""
    payload = httpx.get(f"{server.base_url}/health/system/", timeout=10.0).json()

    assert payload["proxy_status"] == "healthy"
    assert payload["overall_agents_status"] == "healthy"
    assert set(payload["agents"]) == set(server.variant.agent_ids)


def test_manifest_lists_the_agent(server: ServeProcess) -> None:
    """The deployment manifest names the agents the proxy routes to."""
    payload = httpx.get(f"{server.base_url}/.well-known/manifest.json", timeout=10.0).json()

    assert server.variant.agent_id in str(payload)


def test_agent_card_is_served_through_the_proxy(server: ServeProcess) -> None:
    """The card arrives on the proxy route and describes the deployed agent."""
    payload = httpx.get(f"{server.agent_url()}/.well-known/agent-card.json", timeout=10.0).json()

    assert payload["name"] == f"Scenario agent ({server.framework.name})"
    assert payload["capabilities"]["streaming"] is True
    assert [skill["id"] for skill in payload["skills"]] == ["commands"]
    assert payload["supportedInterfaces"], "a card with no interface cannot be connected to"


@pytest.mark.command("help")
async def test_help_answers_with_the_menu(client: ScenarioClient) -> None:
    """`help` answers the menu, and the turn completes."""
    events = await client.send("help")

    assert help_text() in "".join(event.text for event in events)
    assert events[-1].state == "COMPLETED"


@pytest.mark.command("echo")
async def test_echo_answers_with_the_argument(client: ScenarioClient) -> None:
    """`echo <text>` answers with the argument, unchanged."""
    events = await client.send("echo hello scenarios")

    replies = [event.text for event in events if event.text]
    assert "hello scenarios" in replies
    assert events[-1].state == "COMPLETED"
