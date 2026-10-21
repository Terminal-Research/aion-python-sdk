"""Fixtures shared across the whole suite."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.environment import isolate

TESTS_ROOT = Path(__file__).resolve().parent

SUITES = {"unit": "unit", "integration": "integration", "scenarios": "scenario"}
"""The three suites: the directory each one lives in, and its marker."""

UNIT_ALLOWED_HOSTS = ["127.0.0.1", "::1", "localhost"]
"""The only hosts a unit test may connect to.

A unit test runs on any machine with nothing set up, so it has no business
with a service anywhere else; pytest-socket fails one that tries, rather than
letting it reach the service.
"""


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Take the machine's configuration out of the environment before collection.

    Collection imports the SDK, and the SDK builds its settings on import, so
    this is the last point at which a developer's credentials, database or
    storage backend can still be kept out of every test. See
    ``tests/environment.py``.
    """
    isolate()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Put the suite marker on every item, from the directory it lives in.

    A test is in a suite by where it is, and nowhere else: the Make targets
    select by directory, and the marker is here so that ``-m`` can still
    combine suites and so that no module can claim a suite it is not in. A
    test file outside the three directories is an error, not a fourth suite,
    and so is a suite marker written on a test: with one written on, the item
    would carry two and answer to both ``-m`` selections.

    ``tryfirst``: the deselection ``-m`` asks for happens in this same hook,
    and the marker has to be there by then.
    """
    for item in items:
        try:
            top = item.path.resolve().relative_to(TESTS_ROOT).parts[0]
        except ValueError:
            continue
        marker = SUITES.get(top)
        if marker is None:
            raise pytest.UsageError(
                f"{item.nodeid}: a test lives under tests/unit, tests/integration "
                f"or tests/scenarios, not under tests/{top}"
            )
        written = [name for name in SUITES.values() if item.get_closest_marker(name)]
        if written:
            raise pytest.UsageError(
                f"{item.nodeid}: carries @pytest.mark.{written[0]}; the suite comes "
                f"from the directory, do not write it"
            )
        item.add_marker(getattr(pytest.mark, marker), append=False)
        if marker == "unit":
            item.add_marker(pytest.mark.allow_hosts(UNIT_ALLOWED_HOSTS))


# Top-level import names of the third-party libraries the server extras
# install. Blocking them turns this environment into a base install for the
# duration of one subprocess.
SERVER_LIBRARIES = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "greenlet",
    "starlette",
    "sse_starlette",
    "opentelemetry",
    "cryptography",
    "asgi_proxy",
    "logstash_async",
)


# Installed in the child interpreter through sitecustomize, before anything
# else imports: a meta path finder that refuses the named modules the way an
# absent installation would.
_BLOCKER = '''\
import sys
from importlib.abc import MetaPathFinder

BLOCKED = {blocked!r}


class _Blocker(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in BLOCKED or any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _Blocker())
'''


@pytest.fixture
def run_python_without(
    tmp_path: Path,
) -> Callable[[Sequence[str], str], subprocess.CompletedProcess]:
    """Run Python code in an interpreter where some modules cannot be imported.

    The one honest way to test what a thinner install does. Extras only add
    third-party libraries, so an install without one is this environment minus
    a handful of importable names - but the suite has already imported half of
    them by the time any test runs, and unpicking sys.modules in process would
    describe nothing real. A subprocess starts clean.
    """

    def run(blocked: Sequence[str], code: str) -> subprocess.CompletedProcess:
        (tmp_path / "sitecustomize.py").write_text(
            _BLOCKER.format(blocked=set(blocked)), encoding="utf-8"
        )
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
        )

    return run


@pytest.fixture(autouse=True)
def extension_registry():
    """The A2A extension registry as a server fills it before it activates anything.

    Nothing registers at import: a server registers the protocol extensions
    while it builds its application. Every test starts from that registry -
    the protocol extensions, freshly registered with their defaults - and
    whatever it registers, activates or marks is gone after it.
    """
    from aion.core.runtime import aion_a2a_extension_registry, register_protocol_extensions

    aion_a2a_extension_registry.clear()
    register_protocol_extensions(aion_a2a_extension_registry)
    try:
        yield aion_a2a_extension_registry
    finally:
        aion_a2a_extension_registry.clear()


@pytest.fixture
def isolated_registry(extension_registry):
    """The extension registry, for a test that registers, activates or marks extensions in it."""
    return extension_registry
