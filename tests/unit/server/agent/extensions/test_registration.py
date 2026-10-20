"""Registering the extensions at startup, activation by ``aion.yaml``, and creating the executors with their options."""

from __future__ import annotations

import dataclasses
from typing import ClassVar, Optional

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from aion.core.config import AgentConfig
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1, EVENT_EXTENSION_URI_V1
from aion.core.exceptions import ConfigurationError
from aion.core.runtime.context.extensions import ExtensionDescriptor
from aion.server.agent.execution.extensions import (
    EXECUTABLE_EXTENSIONS,
    ExtensionServices,
    create_extension_executors,
    parse_options,
    register_extensions,
)
from aion.server.agent.execution.extensions.evolution import EVOLUTION_EXTENSION

from tests.support.scripted_executor.contract import URI
from tests.support.scripted_executor.executor import SCRIPTED, ScriptedExecutor


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "fast"
    limit: int = 3


class WithOptions(ScriptedExecutor):
    def __init__(self, options=None, services=None, **kwargs) -> None:
        super().__init__(None, services, **kwargs)
        self.options = parse_options(Options, URI, options or {})


class Unavailable(ScriptedExecutor):
    unavailable: ClassVar[Optional[str]] = "the backend is not installed"


class ConstructorRaises(ScriptedExecutor):
    def __init__(self, options=None, services=None, **kwargs):
        raise RuntimeError("cannot start")


def descriptor(executor: type[ScriptedExecutor] = ScriptedExecutor):
    return dataclasses.replace(SCRIPTED, executor=executor)


def config(*enabled) -> AgentConfig:
    return AgentConfig(path="agent:graph", enabled_extensions=list(enabled))


def register(agent_config: AgentConfig, *executables, registry):
    return register_extensions(agent_config, registry=registry, executables=executables or (SCRIPTED,))


def registered(registry, uri: str) -> ExtensionDescriptor:
    return next(item for item in registry.get_all() if item.uri == uri)


def test_the_server_ships_the_evolution_extension() -> None:
    assert EXECUTABLE_EXTENSIONS == (EVOLUTION_EXTENSION,)
    assert EVOLUTION_EXTENSION.requires == (DAEMON_EXTENSION_URI_V1,)
    assert EVOLUTION_EXTENSION.active is False


# -- registration ---------------------------------------------------------------------


def test_the_registry_is_filled_afresh_with_the_protocol_and_executable_extensions(isolated_registry) -> None:
    isolated_registry.register(ExtensionDescriptor(uri="https://example.invalid/left-over"))

    register(config(), registry=isolated_registry)

    uris = {item.uri for item in isolated_registry.get_all()}
    assert {EVENT_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1, URI} <= uris
    assert "https://example.invalid/left-over" not in uris


def test_an_extension_the_agent_does_not_enable_is_registered_inactive(isolated_registry) -> None:
    enabled = register(config(), registry=isolated_registry)

    assert enabled == []
    assert registered(isolated_registry, URI).active is False


def test_an_enabled_extension_is_active_and_returned_with_its_options(isolated_registry) -> None:
    [extension] = register(config({URI: {"mode": "slow"}}), registry=isolated_registry)

    assert registered(isolated_registry, URI).active
    assert (extension.descriptor.uri, dict(extension.options)) == (URI, {"mode": "slow"})
    assert isolated_registry.unservable_reason(URI) is None


@pytest.mark.parametrize("block", [{}, None], ids=["empty-block", "null-block"])
def test_an_enabled_extension_without_options_gets_none(isolated_registry, block) -> None:
    [extension] = register(config({URI: block}), registry=isolated_registry)

    assert dict(extension.options) == {}


def test_options_given_to_a_protocol_extension_stop_the_startup(isolated_registry) -> None:
    with pytest.raises(ConfigurationError, match=f"{DAEMON_EXTENSION_URI_V1}, which takes none"):
        register(config({DAEMON_EXTENSION_URI_V1: {"x": 1}}), registry=isolated_registry)


def test_a_protocol_extension_listed_with_an_empty_block_is_enabled(isolated_registry) -> None:
    register(config({DAEMON_EXTENSION_URI_V1: None}), registry=isolated_registry)

    assert registered(isolated_registry, DAEMON_EXTENSION_URI_V1).active


# -- creating the executors ------------------------------------------------------------------


def test_every_enabled_extension_gets_its_executor_with_the_services(isolated_registry) -> None:
    services = ExtensionServices()

    executors = create_extension_executors(register(config(URI), registry=isolated_registry), services)

    assert list(executors) == [URI]
    assert isinstance(executors[URI], ScriptedExecutor)
    assert executors[URI].services is services


def test_the_options_reach_the_executor_validated(isolated_registry) -> None:
    enabled = register(config({URI: {"mode": "slow", "limit": "5"}}), descriptor(WithOptions), registry=isolated_registry)

    executors = create_extension_executors(enabled, ExtensionServices())

    assert executors[URI].options == Options(mode="slow", limit=5)


@pytest.mark.parametrize(
    ("options", "named"),
    [
        ({"mode": "slow", "colour": "red", "size": 1}, "colour, size"),
        ({"limit": "many"}, "limit"),
    ],
    ids=["unknown-options", "value-not-allowed"],
)
def test_options_the_executor_refuses_stop_the_startup(isolated_registry, options, named) -> None:
    enabled = register(config({URI: options}), descriptor(WithOptions), registry=isolated_registry)

    with pytest.raises(ConfigurationError) as error:
        create_extension_executors(enabled, ExtensionServices())

    assert URI in str(error.value)
    assert named in str(error.value)


def test_options_given_to_an_executor_that_takes_none_stop_the_startup(isolated_registry) -> None:
    enabled = register(config({URI: {"mode": "slow"}}), registry=isolated_registry)

    with pytest.raises(ConfigurationError, match="does not take: mode"):
        create_extension_executors(enabled, ExtensionServices())


@pytest.mark.parametrize(
    ("executor", "reason"),
    [
        (Unavailable, "the backend is not installed"),
        (ConstructorRaises, "cannot start"),
    ],
    ids=["executor-says-it-cannot-run", "executor-fails"],
)
def test_an_extension_that_cannot_work_here_stops_the_startup_with_the_reason(isolated_registry, executor, reason) -> None:
    enabled = register(config(URI), descriptor(executor), registry=isolated_registry)

    with pytest.raises(ConfigurationError) as error:
        create_extension_executors(enabled, ExtensionServices())

    assert URI in str(error.value)
    assert reason in str(error.value)


# -- aion.yaml ----------------------------------------------------------------------


def test_enabled_extensions_take_uris_and_uris_with_options() -> None:
    agent = config(DAEMON_EXTENSION_URI_V1, {URI: {"mode": "slow"}}, {"https://example.invalid/b": None})

    assert agent.enabled_extension_uris == [DAEMON_EXTENSION_URI_V1, URI, "https://example.invalid/b"]
    assert agent.extension_options == {
        DAEMON_EXTENSION_URI_V1: {},
        URI: {"mode": "slow"},
        "https://example.invalid/b": {},
    }


@pytest.mark.parametrize(
    ("enabled", "message"),
    [
        ([URI, {URI: None}], "listed in enabled_extensions twice"),
        ([{URI: None, DAEMON_EXTENSION_URI_V1: None}], "exactly one extension URI"),
        ([""], "names no extension URI"),
    ],
    ids=["twice", "two-uris-in-one-entry", "empty"],
)
def test_an_enabled_extensions_entry_that_names_no_single_extension_is_refused(enabled, message) -> None:
    with pytest.raises(ValidationError, match=message):
        config(*enabled)
