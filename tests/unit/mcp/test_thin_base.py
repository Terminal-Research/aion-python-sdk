"""aion.mcp in an install that has no server extras.

The MCP endpoints are part of the base install; the ASGI proxy is not. Keeping
that true means the proxy libraries must stay out of the import path until
load_proxy() is actually called - and that when it is called, the failure names
the extras that would fix it instead of a module nobody has heard of.
"""

from __future__ import annotations


PROXY_LIBRARIES = ("asgi_proxy",)


def test_importing_mcp_does_not_load_the_proxy_libraries(run_python_without) -> None:
    """Nothing about importing aion.mcp reaches the proxy module."""
    result = run_python_without(
        (),
        """
        import sys
        import aion.mcp

        loaded = sorted(m for m in sys.modules if m.startswith(("asgi_proxy", "aion.mcp.proxy")))
        print(loaded)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_mcp_imports_with_the_proxy_libraries_absent(run_python_without) -> None:
    """A base install has neither library, and aion.mcp still works there."""
    result = run_python_without(
        PROXY_LIBRARIES,
        """
        from aion.mcp import aion_mcp_endpoint, load_proxy

        print(callable(aion_mcp_endpoint), callable(load_proxy))
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True True"


def test_load_proxy_needs_the_proxy_libraries(run_python_without) -> None:
    """Calling it is where the absence shows up, and it has to be actionable.

    The proxy libraries arrive with the server extras, and nothing this far
    down knows which framework the agent uses, so both are named. ``[server]``
    is not: it installs a server with no framework behind it.
    """
    result = run_python_without(
        PROXY_LIBRARIES,
        """
        import aion.mcp

        try:
            aion.mcp.load_proxy()
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
            print("cause:", type(exc.__cause__).__name__, exc.__cause__.name)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "MissingOptionalDependency" in result.stdout
    assert "aion.mcp.load_proxy requires optional dependencies." in result.stdout
    assert 'pip install "aionto-sdk[langgraph-server]"' in result.stdout
    assert 'pip install "aionto-sdk[adk-server]"' in result.stdout
    assert "[server]" not in result.stdout
    assert "cause: ModuleNotFoundError asgi_proxy" in result.stdout


def test_a_missing_aion_module_is_not_reported_as_an_extra(run_python_without) -> None:
    """An incomplete wheel is a different problem and must read like one."""
    result = run_python_without(
        ("aion.mcp.proxy",),
        """
        import aion.mcp

        try:
            aion.mcp.load_proxy()
        except ImportError as exc:
            print(type(exc).__name__)
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ModuleNotFoundError")
    assert "pip install" not in result.stdout
