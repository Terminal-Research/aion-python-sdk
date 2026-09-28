"""Importing the toolkit without the ADK extra installed."""

from __future__ import annotations


ADK_LIBRARIES = ("google.adk", "litellm")


def test_import_names_the_extra(run_python_without) -> None:
    """The failure has to name the install command, not a stranger's module."""
    result = run_python_without(
        ADK_LIBRARIES,
        """
        try:
            import aion.adk.authoring
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "MissingOptionalDependency" in result.stdout
    assert "Google ADK authoring requires optional dependencies." in result.stdout
    assert 'pip install "aionto-sdk[adk-authoring]"' in result.stdout


def test_a_missing_aion_module_is_not_reported_as_an_extra(run_python_without) -> None:
    """An incomplete wheel is a different problem and must read like one."""
    result = run_python_without(
        ("aion.adk.authoring.mcp",),
        """
        try:
            import aion.adk.authoring
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ModuleNotFoundError")
    assert "pip install" not in result.stdout
