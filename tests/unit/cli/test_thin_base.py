"""The CLI in an install that has no server extras.

`pip install aionto-sdk` brings every aion.* subpackage but only the base
third-party dependencies. Loading a server entry point then fails on a
library such as fastapi or sse_starlette, while importing the CLI stays
lightweight. The run_python_without fixture reproduces that shape.
"""

from __future__ import annotations

import pytest

from tests.conftest import SERVER_LIBRARIES


def test_cli_module_imports_without_the_server_extra(run_python_without) -> None:
    """Importing the entry point must not reach aion.server."""
    result = run_python_without(
        SERVER_LIBRARIES,
        """
        import aion.cli.cli
        import sys
        assert "aion.server" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("aion.server")
        )
        print("imported")
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_help_works_without_the_server_extra(run_python_without) -> None:
    """`aion --help` is where the user reads which extra they need."""
    result = run_python_without(
        SERVER_LIBRARIES,
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["--help"])
        print(outcome.exit_code)
        print(outcome.output)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("0\n")
    for command in ("serve", "chat", "logs"):
        assert command in result.stdout


def test_serve_asks_for_the_server_extra(run_python_without) -> None:
    """A third-party library missing from a base install is not a traceback."""
    result = run_python_without(
        SERVER_LIBRARIES,
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["serve"])
        print(outcome.exit_code)
        print(outcome.output)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert not result.stdout.startswith("0\n")
    assert "aion serve requires optional dependencies." in result.stdout
    # Both, because nothing here knows which framework the agents use - and
    # never [server], which would install a server with no framework behind it.
    assert 'pip install "aionto-sdk[langgraph-server]"' in result.stdout
    assert 'pip install "aionto-sdk[adk-server]"' in result.stdout
    assert "[server]" not in result.stdout


def test_serve_does_not_hide_a_missing_aion_module(run_python_without) -> None:
    """Our own code missing is a packaging defect, and must not read as an extra."""
    result = run_python_without(
        ("aion.server",),
        """
        from asyncclick.testing import CliRunner
        import anyio
        from aion.cli.cli import cli

        outcome = anyio.run(CliRunner().invoke, cli, ["serve"])
        print(outcome.exit_code)
        print(type(outcome.exception).__name__, outcome.exception)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" in result.stdout
    assert "aion.server" in result.stdout
    assert "pip install" not in result.stdout


@pytest.mark.parametrize("module", ["aion.core", "aion.api", "aion.mcp"])
def test_base_packages_import_without_the_server_extra(module: str, run_python_without) -> None:
    """What the base install advertises has to import in a base install."""
    result = run_python_without(
        SERVER_LIBRARIES,
        f"""
        import {module}
        print("imported")
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


@pytest.mark.parametrize("name", ["run_server", "app_registry"])
def test_server_entry_points_still_explain_missing_extras(name, run_python_without):
    result = run_python_without(
        SERVER_LIBRARIES,
        f"""
        from aion.core.utils.optional_deps import MissingOptionalDependency
        import aion.server
        try:
            getattr(aion.server, {name!r})
        except MissingOptionalDependency as exc:
            assert 'aion.server requires optional dependencies' in str(exc)
            assert 'aionto-sdk[langgraph-server]' in str(exc)
            assert 'aionto-sdk[adk-server]' in str(exc)
        else:
            raise AssertionError('server entry point loaded without its dependencies')
        """,
    )
    assert result.returncode == 0, result.stderr


def test_startup_logging_does_not_import_application_or_database(run_python_without):
    result = run_python_without(
        ("aion.db", "aion.server.agent", "aion.server.core.app", "aion.server.server"),
        """
        import logging
        import os
        import sys
        os.environ['POSTGRES_URL'] = 'not-a-database-url'
        from aion.core.logging import set_process_role
        from aion.server.logging import setup_root_logger
        set_process_role('CLI')
        setup_root_logger()
        logging.getLogger('startup').error('startup probe')
        assert 'aion.db.settings' not in sys.modules
        assert 'aion.server.agent' not in sys.modules
        """,
    )
    assert result.returncode == 0, result.stderr
    assert 'CLI - startup probe' in result.stderr
    assert 'Logging error' not in result.stderr
