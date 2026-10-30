"""Does a deployment of this framework answer at all."""

from __future__ import annotations

import httpx
import pytest

from tests.scenarios.commands import help_text
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant, control_plane
from tests.scenarios.harness.serve import serve

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


def test_every_registration_response_identifies_the_same_runtime(framework) -> None:
    """Separate proxy and agent processes prove the same admitted input."""
    version = "00000000-0000-0000-0000-000000000001"
    attempt = "00000000-0000-0000-0000-000000000002"
    variant = ServeVariant(env={
        "VERSION_ID": version, "AION_RUNTIME_ATTEMPT_ID": attempt,
    })
    with serve(framework, variant) as runtime:
        for path in ("/.well-known/manifest.json", "/health/system/", "/health/"):
            response = httpx.get(f"{runtime.base_url}{path}", timeout=10)
            assert response.status_code == 200
            assert response.headers["Aion-Runtime-Attempt-Id"] == attempt
            if path != "/health/":
                assert response.json()["runtime"] == {
                    "versionId": version, "runtimeAttemptId": attempt}
        for path in ("/.well-known/agent-card.json", "/.well-known/configuration.json", "/health/"):
            response = httpx.get(f"{runtime.agent_url()}{path}", timeout=10)
            assert response.status_code == 200
            assert response.headers["Aion-Runtime-Attempt-Id"] == attempt


def test_agent_card_is_served_through_the_proxy(server: ServeProcess) -> None:
    """The card arrives on the proxy route and describes the deployed agent."""
    payload = httpx.get(f"{server.agent_url()}/.well-known/agent-card.json", timeout=10.0).json()

    assert payload["name"] == f"Scenario agent ({server.framework.name})"
    assert payload["capabilities"]["streaming"] is True
    assert [skill["id"] for skill in payload["skills"]] == ["commands"]
    assert payload["supportedInterfaces"], "a card with no interface cannot be connected to"


def test_the_card_asks_for_a_bearer_token(server: ServeProcess) -> None:
    """Every call needs a token, and the card says so before the first one is refused."""
    payload = httpx.get(f"{server.agent_url()}/.well-known/agent-card.json", timeout=10.0).json()

    assert payload["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_there_is_no_api_browser(server: ServeProcess, path: str) -> None:
    headers = {"Authorization": f"Bearer {control_plane().token()}"}
    response = httpx.get(f"{server.agent_url()}{path}", headers=headers, timeout=10.0)

    assert response.status_code == 404


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


@pytest.mark.command("echo")
async def test_an_a2a_0_3_client_is_answered_too(client: ScenarioClient) -> None:
    """The card names a 0.3 interface; a 0.3 `message/send` through the proxy completes."""
    response = await client.rpc_v03(
        "message/send",
        {
            "message": {
                "kind": "message",
                "messageId": "scenario-v03",
                "role": "user",
                "parts": [{"kind": "text", "text": "echo from 0.3"}],
            }
        },
    )

    task = response["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    assert "from 0.3" in [part.get("text") for part in task["status"]["message"]["parts"]]
