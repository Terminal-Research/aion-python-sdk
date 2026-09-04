"""aion.mcp in an install that has no server extras.

The MCP endpoints are part of the base install; the ASGI proxy is not. Keeping
that true means the proxy libraries must stay out of the import path until
load_proxy() is actually called.
"""

from __future__ import annotations


PROXY_LIBRARIES = ("asgi_proxy_lib", "asgi_proxy")


def test_importing_mcp_does_not_load_the_proxy_libraries(run_python_without) -> None:
    """Nothing about importing aion.mcp reaches asgi_proxy_lib."""
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
    """Calling it is where the absence shows up, and only there."""
    result = run_python_without(
        PROXY_LIBRARIES,
        """
        import aion.mcp

        try:
            aion.mcp.load_proxy()
        except ImportError as exc:
            print(type(exc).__name__, exc.name)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ModuleNotFoundError asgi_proxy"
