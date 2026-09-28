"""Configuration variable delivery through the distribution extension.

``aion.yaml`` declares the shape of an agent's configuration; the values are
a runtime concern, and the platform sends them per request, in the
distribution extension payload's ``environment.configurationVariables``. The
agent reads them through
``AionRuntimeContext.get_environment().get_configuration_variable(key)``.

So the scenarios stand in for the platform rather than for the manifest: what
they check is that the mapping put on the wire is the mapping the agent sees,
value for value. Whether ``aion.yaml`` declares a key, and what ``type:
secret`` means for the declaration, is the configuration model's own contract
and is checked in the unit suite.

A value the platform marks secret is sent in plaintext like any other, which
is the case worth stating: there is nothing between the wire and the agent
that may mask, truncate or re-type it.
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

# One environment carrying every shape a value can take. The secret is only a
# key the platform would have marked secret: the wire treats it as a string
# like the rest, and nothing here prints it.
SECRET_KEY = "SCENARIO_API_SECRET"
SECRET_VALUE = "sk-scenario-0123456789-not-a-real-credential"
EMPTY_KEY = "SCENARIO_EMPTY"
NUMERIC_KEY = "SCENARIO_RETRIES"
NUMERIC_VALUE = "3"

ENVIRONMENT = {
    CONFIG_KEY: CONFIG_VALUE,
    SECRET_KEY: SECRET_VALUE,
    EMPTY_KEY: "",
    NUMERIC_KEY: NUMERIC_VALUE,
}


def config_response(events: list[Ev]) -> ConfigResponse:
    """The structured answer the ``config`` command sent back."""
    return ConfigResponse.model_validate_json(reply_texts(events)[-1])


async def read_config(client: ScenarioClient, key: str) -> ConfigResponse:
    """Ask the agent for one key out of the environment above."""
    events = await client.send(
        f"config {key}",
        metadata=distribution_metadata(configuration_variables=ENVIRONMENT),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )
    assert final_task(events).state == "COMPLETED"
    return config_response(events)


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


@pytest.mark.command("config")
async def test_every_variable_of_an_environment_reaches_the_agent(
    client: ScenarioClient,
) -> None:
    """One payload carries many keys, and each of them arrives under its own name."""
    for key, expected in ENVIRONMENT.items():
        response = await read_config(client, key)
        assert response.key == key
        assert response.present is True
        assert response.value == expected


@pytest.mark.command("config")
async def test_a_secret_value_arrives_whole(client: ScenarioClient) -> None:
    """Nothing between the wire and the agent masks or truncates a secret."""
    response = await read_config(client, SECRET_KEY)

    assert response.value == SECRET_VALUE
    assert len(response.value or "") == len(SECRET_VALUE)


@pytest.mark.command("config")
async def test_an_empty_value_is_a_value(client: ScenarioClient) -> None:
    """An empty string is a configured value, not an absent key."""
    response = await read_config(client, EMPTY_KEY)

    assert response.present is True
    assert response.value == ""


@pytest.mark.command("config")
async def test_values_arrive_as_the_strings_they_were_sent_as(
    client: ScenarioClient,
) -> None:
    """The contract is a mapping of strings; nothing re-types a value in transit."""
    response = await read_config(client, NUMERIC_KEY)

    assert response.value == NUMERIC_VALUE
    assert isinstance(response.value, str)
