"""Importing the toolkit without the LangGraph extra installed."""

from __future__ import annotations


LANGGRAPH_LIBRARIES = (
    "langgraph",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_mcp_adapters",
)


def test_import_names_the_extra(run_python_without) -> None:
    """The failure has to name the install command, not a stranger's module."""
    result = run_python_without(
        LANGGRAPH_LIBRARIES,
        """
        try:
            import aion.langgraph.authoring
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "MissingOptionalDependency" in result.stdout
    assert "LangGraph authoring requires optional dependencies." in result.stdout
    assert 'pip install "aionto-sdk[langgraph-authoring]"' in result.stdout


def test_import_keeps_the_original_failure(run_python_without) -> None:
    """The library that was actually missing stays reachable through __cause__."""
    result = run_python_without(
        LANGGRAPH_LIBRARIES,
        """
        try:
            import aion.langgraph.authoring
        except ImportError as exc:
            print(type(exc.__cause__).__name__, exc.__cause__.name)
        """,
    )

    assert result.returncode == 0, result.stderr
    name, missing = result.stdout.split()
    assert name == "ModuleNotFoundError"
    assert missing in LANGGRAPH_LIBRARIES


def test_a_missing_aion_module_is_not_reported_as_an_extra(run_python_without) -> None:
    """An incomplete wheel is a different problem and must read like one."""
    result = run_python_without(
        ("aion.langgraph.authoring.handlers",),
        """
        try:
            import aion.langgraph.authoring
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ModuleNotFoundError")
    assert "pip install" not in result.stdout
