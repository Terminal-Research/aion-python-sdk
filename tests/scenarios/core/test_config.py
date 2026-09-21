"""Configuration variable delivery through the distribution extension.

The platform sends configuration variables as part of the distribution
extension payload on every request.  The agent reads them through
``AionRuntimeContext.get_environment().get_configuration_variable(key)``.

These scenarios verify that the value the platform put on the wire is the
value the agent sees, including the absent-key and no-distribution edges.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import ConfigResponse
from tests.scenarios.harness import (
    DISTRIBUTION_EXTENSION_URI,
    Ev,
    ScenarioClient,
    distribution_metadata,
    final_task,
    reply_texts,
)

pytestmark = pytest.mark.config

CONFIG_KEY = "SCENARIO_GREETING"
CONFIG_VALUE = "hello from the platform"


def config_response(events: list[Ev]) -> ConfigResponse:
    """The structured answer the ``config`` command sent back."""
    return ConfigResponse.model_validate_json(reply_texts(events)[-1])


@pytest.mark.command("config")
async def test_config_variable_reaches_the_agent(client: ScenarioClient) -> None:
    """A key present in the distribution payload is readable by the agent."""
    events = await client.send(
        f"config {CONFIG_KEY}",
        metadata=distribution_metadata(
            configuration_variables={CONFIG_KEY: CONFIG_VALUE},
        ),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    response = config_response(events)
    assert response.key == CONFIG_KEY
    assert response.present is True
    assert response.value == CONFIG_VALUE
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("config")
async def test_absent_config_variable_is_reported_as_missing(client: ScenarioClient) -> None:
    """A key that the platform did not send comes back ``present=False``."""
    events = await client.send(
        "config NEVER_SET",
        metadata=distribution_metadata(
            configuration_variables={CONFIG_KEY: CONFIG_VALUE},
        ),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    response = config_response(events)
    assert response.key == "NEVER_SET"
    assert response.present is False
    assert response.value is None


@pytest.mark.command("config")
async def test_config_without_distribution_sees_no_environment(client: ScenarioClient) -> None:
    """Without the distribution extension the environment is absent entirely."""
    events = await client.send("config ANYTHING")

    response = config_response(events)
    assert response.present is False
    assert response.value is None
