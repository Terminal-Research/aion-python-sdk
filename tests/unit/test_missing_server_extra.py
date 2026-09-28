"""Importing the server layer in an install that has no server extras.

`pip install aionto-sdk` brings every aion.* subpackage but only the base
third-party dependencies, so importing aion.server, aion.db.postgres or
aion.proxy there fails on a library such as fastapi or sqlalchemy - not on
Aion code. What the reader must be told is which extra to install, and there
are two of them: nothing at this depth knows which framework the agent uses.

The file sits at the top of the suite because its subject spans three
subpackages and belongs to none of them.
"""

from __future__ import annotations

import pytest

from tests.conftest import SERVER_LIBRARIES


# The packages that guard their imports, and the third-party library each one
# is expected to trip over first in a base install.
GUARDED_PACKAGES = ("aion.server", "aion.db.postgres", "aion.proxy")


@pytest.mark.parametrize("package", GUARDED_PACKAGES)
def test_import_names_both_server_extras(package: str, run_python_without) -> None:
    """The failure has to name install commands, not a stranger's module."""
    result = run_python_without(
        SERVER_LIBRARIES,
        f"""
        try:
            import {package}
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "MissingOptionalDependency" in result.stdout
    assert f"{package} requires optional dependencies." in result.stdout
    assert 'pip install "aionto-sdk[langgraph-server]"' in result.stdout
    assert 'pip install "aionto-sdk[adk-server]"' in result.stdout


@pytest.mark.parametrize("package", GUARDED_PACKAGES)
def test_import_never_names_the_internal_server_extra(
    package: str, run_python_without
) -> None:
    """[server] installs a server with no framework behind it - never the answer."""
    result = run_python_without(
        SERVER_LIBRARIES,
        f"""
        try:
            import {package}
        except ImportError as exc:
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "[server]" not in result.stdout


@pytest.mark.parametrize("package", GUARDED_PACKAGES)
def test_import_keeps_the_original_failure(package: str, run_python_without) -> None:
    """The library that was actually missing stays reachable through __cause__."""
    result = run_python_without(
        SERVER_LIBRARIES,
        f"""
        try:
            import {package}
        except ImportError as exc:
            cause = exc.__cause__
            print(type(cause).__name__, cause.name)
        """,
    )

    assert result.returncode == 0, result.stderr
    name, missing = result.stdout.split()
    assert name == "ModuleNotFoundError"
    assert missing.split(".")[0] in SERVER_LIBRARIES


@pytest.mark.parametrize(
    ("package", "own_module"),
    [
        ("aion.server", "aion.server.server"),
        ("aion.db.postgres", "aion.db.postgres.manager"),
        ("aion.proxy", "aion.proxy.handlers"),
    ],
)
def test_a_missing_aion_module_is_not_reported_as_an_extra(
    package: str, own_module: str, run_python_without
) -> None:
    """An incomplete wheel is a different problem and must read like one."""
    result = run_python_without(
        (own_module,),
        f"""
        try:
            import {package}
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ModuleNotFoundError")
    assert "pip install" not in result.stdout


@pytest.mark.parametrize(
    ("package", "inner"),
    [
        # aion.server reaches aion.db.postgres, whose guard fires first when
        # only the database libraries are missing.
        ("aion.server", "aion.db.postgres"),
        # aion.proxy reads the server's settings, so aion.server's guard fires.
        ("aion.proxy", "aion.server"),
    ],
)
def test_import_is_reported_under_the_package_that_was_imported(
    package: str, inner: str, run_python_without
) -> None:
    """A guard tripped further down is re-raised under the name the reader used.

    Only sqlalchemy is blocked, so the first failure is inside the inner
    package's guard rather than in ``package``'s own imports. The extras are
    the same either way; the feature name must be the one that was imported.
    """
    result = run_python_without(
        ("sqlalchemy",),
        f"""
        try:
            import {package}
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
            print("cause:", type(exc.__cause__).__name__, exc.__cause__.name)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "MissingOptionalDependency" in result.stdout
    assert f"{package} requires optional dependencies." in result.stdout
    assert f"{inner} requires optional dependencies." not in result.stdout
    # The wrapping keeps the missing library's name reachable.
    assert "cause: MissingOptionalDependency sqlalchemy" in result.stdout
