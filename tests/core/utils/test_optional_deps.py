"""Tests for the optional-dependency helpers."""

from __future__ import annotations

import pytest

from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_extra_error,
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
