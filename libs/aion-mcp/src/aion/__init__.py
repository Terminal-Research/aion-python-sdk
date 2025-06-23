"""Namespace package for Aion MCP utilities."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .mcp import load_proxy  # noqa: E402

__all__ = ["load_proxy"]
