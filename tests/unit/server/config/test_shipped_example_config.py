"""The example `aion.yaml` is a configuration this SDK accepts.

`aion.yaml.example` is what someone copies to start, so a key in it is a key
they will believe in. It used to be LangGraph Platform's `langgraph.json`
schema with the `aion:` root swapped in - `dependencies`, `agent`, `http`,
`env` - and it parsed to zero agents, because `extra="ignore"` dropped every
one of those keys without a word. That is also where `aion.http` entered the
published settings table: copied out of a file describing another product.

So this loads the shipped file through the reader `aion serve` uses and states
what it should contain. A key that stops meaning something fails here rather
than in the first project that copied it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aion.core.config.reader import AionConfigReader

EXAMPLE = Path(__file__).resolve().parents[4] / "aion.yaml.example"


@pytest.fixture(scope="module")
def config():
    """The shipped example, loaded the way the server loads a configuration."""
    assert EXAMPLE.exists(), f"no example configuration at {EXAMPLE}"
    return AionConfigReader(config_path=EXAMPLE).load_and_validate_config()


def test_the_example_declares_the_agents_it_appears_to(config) -> None:
    """Both agents in the file are agents the SDK parsed, keyed by their ids."""
    assert list(config.agents) == ["support", "onboarding"]


def test_every_agent_has_the_one_required_field(config) -> None:
    """`path` is what makes an entry an agent, and both entries have one."""
    for agent_id, agent in config.agents.items():
        assert agent.path, f"agent {agent_id!r} has no import target"
        assert ":" in agent.path, f"agent {agent_id!r} names no symbol in {agent.path!r}"


def test_the_fully_specified_agent_keeps_every_field_it_declares(config) -> None:
    """The example's showcase agent is read whole, not partially ignored."""
    support = config.agents["support"]

    assert support.name == "Support Agent"
    assert support.version == "1.2.0"
    assert [skill.id for skill in support.skills] == ["answer-question"]
    assert sorted(support.configuration) == ["api_token", "max_retries", "model"]
    assert support.configuration["max_retries"].max == 10
    assert support.configuration["api_token"].type == "secret"
    assert support.enabled_extensions == [
        "https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0"
    ]


def test_the_minimal_agent_is_a_path_and_defaults(config) -> None:
    """The second entry shows that everything except `path` is optional."""
    onboarding = config.agents["onboarding"]

    assert onboarding.name == "Agent"
    assert onboarding.input_modes == ["text"]
    assert onboarding.skills == []


def test_the_mcp_section_is_read(config) -> None:
    """`aion.mcp.port` is a declared key, not a second parser's private one."""
    assert config.mcp is not None
    assert config.mcp.port == 9000


def test_an_unknown_key_added_to_the_example_would_fail(tmp_path) -> None:
    """The guarantee behind all of the above: nothing here is silently dropped.

    Without this, every assertion in this module would still hold for a file
    that had grown a key the SDK ignores - which is exactly the state the
    example was in.
    """
    from aion.core.config.exceptions import ConfigurationError

    copy = tmp_path / "aion.yaml"
    shutil.copy(EXAMPLE, copy)
    copy.write_text(copy.read_text() + "\n  http:\n    /api/custom: 'my_project.web:app'\n")

    with pytest.raises(ConfigurationError) as failure:
        AionConfigReader(config_path=copy).load_and_validate_config()

    assert "http" in str(failure.value.details)
