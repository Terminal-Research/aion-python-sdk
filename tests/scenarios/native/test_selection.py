"""The native scenarios run the native agent, and the one the run asked for."""

from __future__ import annotations

import pytest

from tests.scenarios.frameworks import FRAMEWORKS_BY_NAME, NATIVE_AGENTS_BY_NAME, Framework
from tests.scenarios.harness import ServeProcess

pytestmark = pytest.mark.native


def test_the_server_runs_the_native_agent_of_this_framework(
    framework: Framework, server: ServeProcess
) -> None:
    """The deployment the server was started from names the native agent.

    Not a formality: ``server`` is a session fixture, and an inherited one
    would hand this directory the server already started for the
    command-contract agent of the same framework, with every native scenario
    then passing or failing about the wrong agent.
    """
    config = server.config_path.read_text()

    assert NATIVE_AGENTS_BY_NAME[framework.name].agent_path in config
    assert FRAMEWORKS_BY_NAME[framework.name].agent_path not in config
