"""Fixtures shared by every scenario: a running server, and a client on it."""

from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio

from tests.scenarios.commands import COMMANDS_BY_KEY
from tests.scenarios.frameworks import FRAMEWORKS, Framework, unsupported_reason
from tests.scenarios.harness import ScenarioClient, ServeProcess, ServeVariant
from tests.scenarios.harness.serve import configs_root

KEEP_SERVE = bool(os.environ.get("KEEP_SERVE"))

SCENARIOS_ROOT = Path(__file__).resolve().parent
"""Everything under here is a scenario, whatever an individual module says."""

TIMEOUT_SECONDS = 300
"""A scenario still running after this is hung, not slow."""

_SERVERS: dict[tuple[str, str], ServeProcess] = {}
"""Servers started this session, one per (framework, variant)."""


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every scenario, and refuse a variant marker that names no template.

    Three markers go on the items under this directory and on nothing else:

    ``scenario``    what keeps scenarios out of `make tests`. The deselection
                    `-m` asks for happens in this same hook, which is why this
                    implementation runs ``tryfirst``.
    ``timeout``     a scenario drives real processes over a real socket; one
                    that hangs would otherwise hold the whole run.
    ``asyncio``     on the coroutine ones, with a session-scoped event loop:
                    the client is a session fixture, and an A2A client does
                    not outlive the loop it was built in.

    Marking here rather than in each module is what makes forgetting
    impossible: a scenario is a scenario by where it lives.
    """
    templates = {path.name.removesuffix(".yaml.j2") for path in configs_root().glob("*.yaml.j2")}
    for item in items:
        if not item.path.is_relative_to(SCENARIOS_ROOT):
            continue
        item.add_marker(pytest.mark.timeout(TIMEOUT_SECONDS), append=False)
        item.add_marker(pytest.mark.scenario, append=False)
        # `asyncio_mode = "auto"` has already put a bare `asyncio` marker on
        # the coroutine tests and on nothing else, which is what tells the two
        # apart here. The loop scope goes at the front: the closest marker is
        # the one that decides it.
        if item.get_closest_marker("asyncio") is not None:
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)

        marker = item.get_closest_marker("variant")
        if marker is not None and marker.args[0] not in templates:
            raise pytest.UsageError(
                f"{item.nodeid}: @pytest.mark.variant({marker.args[0]!r}) names no template "
                f"under {configs_root()}"
            )


@pytest.fixture(scope="session", params=[framework.name for framework in FRAMEWORKS])
def framework(request: pytest.FixtureRequest) -> Framework:
    """The framework this run of the scenario is about."""
    return next(item for item in FRAMEWORKS if item.name == request.param)


@pytest.fixture(autouse=True)
def skip_unsupported(request: pytest.FixtureRequest) -> None:
    """Skip a scenario whose command this framework cannot do.

    The reason comes from ``frameworks.UNSUPPORTED`` and nowhere else: a test
    that needs to know which framework it runs on is a contract gap, not a
    conditional.
    """
    marker = request.node.get_closest_marker("command")
    if marker is None:
        return
    key = marker.args[0]
    if key not in COMMANDS_BY_KEY:
        raise pytest.UsageError(f"@pytest.mark.command({key!r}) names no command in the registry")
    if "framework" not in request.fixturenames:
        return
    reason = unsupported_reason(request.getfixturevalue("framework").name, key)
    if reason:
        pytest.skip(reason)


def serve_for(framework: Framework, variant: ServeVariant) -> ServeProcess:
    """The server for this pair, started on first use and kept for the session."""
    key = (framework.name, variant.name)
    if key not in _SERVERS:
        _SERVERS[key] = ServeProcess(framework, variant).start()
    return _SERVERS[key]


@pytest.fixture(scope="session", autouse=True)
def _stop_servers(pytestconfig: pytest.Config) -> Iterator[None]:
    """Take every server started this session away again.

    With ``KEEP_SERVE`` they are left running instead, and where to find each
    one is printed - the port to send another message to, the config it was
    started from, and the log it wrote.
    """
    yield
    reporter = pytestconfig.pluginmanager.get_plugin("terminalreporter")
    for process in _SERVERS.values():
        if not KEEP_SERVE:
            process.stop()
            continue
        if reporter is not None:
            reporter.write_line("")
            reporter.write_line(
                f"[KEEP_SERVE] {process.framework.name}/{process.variant.name} still running",
                bold=True,
            )
            reporter.write_line(f"             url    {process.base_url}")
            reporter.write_line(f"             agent  {process.agent_url()}")
            reporter.write_line(f"             config {process.config_path}")
            reporter.write_line(f"             log    {process.log_path}")
    if not KEEP_SERVE:
        _SERVERS.clear()


@pytest.fixture(scope="session")
def server(framework: Framework) -> ServeProcess:
    """A running `aion serve` with the default deployment of this framework."""
    return serve_for(framework, ServeVariant())


@pytest.fixture(scope="session")
def server_for(framework: Framework) -> Callable[[ServeVariant], ServeProcess]:
    """A server for any other deployment variant of this framework.

    Started on first use and kept for the session like ``server``; a module
    that needs its own variant declares it and asks for it here.
    """
    return lambda variant: serve_for(framework, variant)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client pointed at the default agent through the proxy.

    Declared with pytest-asyncio's own decorator rather than
    ``pytest.fixture``: the repository runs function-scoped loops by default,
    and a session-scoped async fixture has to name the loop it lives in.
    """
    scenario_client = await ScenarioClient.connect(server.base_url, server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Attach the server log to a failing scenario.

    A scenario fails on what the client saw; what the server did about it is
    in its log, and a failure that does not carry it sends the reader looking
    for a temporary directory that teardown has already removed.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    for process in _SERVERS.values():
        report.sections.append(
            (f"aion serve ({process.framework.name}/{process.variant.name})", process.log_tail())
        )
