"""The CLI in an install that has no server extras.

`pip install aionto-sdk` brings every aion.* subpackage but only the base
third-party dependencies, so importing aion.server there fails on a library
such as fastapi or sse_starlette - not on aion.server itself. These tests
reproduce that shape by blocking those libraries in a subprocess: an in-process
block would have to unpick whatever the rest of the suite already imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# Top-level import names of the third-party libraries the [server] extra
# installs. Blocking them turns this environment into a base install for the
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
    "asgi_proxy_lib",
    "logstash_async",
)

BLOCKER = '''\
import sys
from importlib.abc import MetaPathFinder

BLOCKED = {blocked!r}


class _Blocker(MetaPathFinder):
    """Refuse the named modules the way an absent installation would."""

    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in BLOCKED or any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _Blocker())
'''


def run_without(blocked: tuple[str, ...], code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run ``code`` in a subprocess where ``blocked`` cannot be imported."""
    (tmp_path / "sitecustomize.py").write_text(
        BLOCKER.format(blocked=set(blocked)), encoding="utf-8"
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )


def test_cli_module_imports_without_the_server_extra(tmp_path: Path) -> None:
    """Importing the entry point must not reach aion.server."""
    result = run_without(
        SERVER_LIBRARIES,
        """
        import aion.cli.cli
        import sys
        assert "aion.server" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("aion.server")
        )
        print("imported")
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_help_works_without_the_server_extra(tmp_path: Path) -> None:
    """`aion --help` is where the user reads which extra they need."""
    result = run_without(
        SERVER_LIBRARIES,
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["--help"])
        print(outcome.exit_code)
        print(outcome.output)
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("0\n")
    for command in ("serve", "chat", "logs"):
        assert command in result.stdout


def test_serve_asks_for_the_server_extra(tmp_path: Path) -> None:
    """A third-party library missing from a base install is not a traceback."""
    result = run_without(
        SERVER_LIBRARIES,
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["serve"])
        print(outcome.exit_code)
        print(outcome.output)
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert not result.stdout.startswith("0\n")
    assert "aion serve requires optional dependencies." in result.stdout
    assert 'pip install "aionto-sdk[server]"' in result.stdout


def test_serve_does_not_hide_a_missing_aion_module(tmp_path: Path) -> None:
    """Our own code missing is a packaging defect, and must not read as an extra."""
    result = run_without(
        ("aion.server",),
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["serve"])
        print(outcome.exit_code)
        print(type(outcome.exception).__name__, outcome.exception)
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" in result.stdout
    assert "aion.server" in result.stdout
    assert "pip install" not in result.stdout


@pytest.mark.parametrize("module", ["aion.core", "aion.api", "aion.mcp"])
def test_base_packages_import_without_the_server_extra(module: str, tmp_path: Path) -> None:
    """What the base install advertises has to import in a base install."""
    result = run_without(
        SERVER_LIBRARIES,
        f"""
        import {module}
        print("imported")
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
