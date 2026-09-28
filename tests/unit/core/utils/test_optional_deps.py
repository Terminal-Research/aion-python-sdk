"""Tests for the optional-dependency helpers."""

from __future__ import annotations

import pytest

from aion.core.utils.optional_deps import (
    SERVER_EXTRAS,
    MissingOptionalDependency,
    is_own_module,
    missing_extra_error,
    missing_server_extra_error,
)


@pytest.mark.parametrize(
    "name",
    ["aion", "aion.server", "aion.langgraph.authoring", "aion.toolkits.x"],
)
def test_is_own_module_accepts_the_aion_namespace(name: str) -> None:
    """Anything under ``aion`` is code, not an installable dependency."""
    assert is_own_module(name) is True


@pytest.mark.parametrize("name", ["langgraph", "aionic", "aion_server", "", None])
def test_is_own_module_rejects_everything_else(name: str | None) -> None:
    """A third-party module is what an extra installs."""
    assert is_own_module(name) is False


def test_missing_extra_error_names_the_install_command() -> None:
    """The message is the same everywhere, and it is a command to run."""
    error = missing_extra_error("LangGraph authoring", "langgraph-authoring", None)

    assert isinstance(error, MissingOptionalDependency)
    assert isinstance(error, ImportError)
    assert str(error) == (
        "LangGraph authoring requires optional dependencies.\n"
        'Install them with: pip install "aionto-sdk[langgraph-authoring]"'
    )


def test_missing_extra_error_carries_the_missing_module_name() -> None:
    """``name`` survives, so a caller can still tell what was missing."""
    cause = ModuleNotFoundError("No module named 'langgraph'", name="langgraph")

    error = missing_extra_error("LangGraph authoring", "langgraph-authoring", cause)

    assert error.name == "langgraph"


def test_missing_server_extra_error_names_every_server_extra() -> None:
    """No single extra is the answer below the authoring toolkits."""
    error = missing_server_extra_error("aion.server", None)

    assert isinstance(error, MissingOptionalDependency)
    assert str(error).startswith("aion.server requires optional dependencies.")
    for extra in SERVER_EXTRAS:
        assert f'pip install "aionto-sdk[{extra}]"' in str(error)


def test_missing_server_extra_error_never_names_the_internal_extra() -> None:
    """[server] exists for the packaging checks, and buys a server with no framework."""
    assert "[server]" not in str(missing_server_extra_error("aion.server", None))
    assert "server" not in SERVER_EXTRAS


def test_missing_server_extra_error_carries_the_missing_module_name() -> None:
    """`exc.name` is what a caller inspects to tell one absence from another."""
    cause = ModuleNotFoundError("No module named 'fastapi'", name="fastapi")

    error = missing_server_extra_error("aion.server", cause)

    assert error.name == "fastapi"
