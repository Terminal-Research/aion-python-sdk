"""Tests for the aion.mcp proxy loader.

The loader reads `aion.yaml` through `AionConfigReader`, the reader `aion
serve` uses. That is the point of these tests as much as the proxy itself:
there is one definition of what `aion.mcp` looks like, so a port that works
here is a port the model declares and the documentation can describe.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from aion.core.config.exceptions import ConfigurationError


class DummyProxy:
    """Stand-in for the ASGI app asgi_proxy() returns."""

    def __init__(self, url: str) -> None:
        self.url = url


@pytest.fixture
def proxy_mod(monkeypatch):
    """The proxy module, with the ASGI proxy library replaced."""
    monkeypatch.setitem(sys.modules, "asgi_proxy", types.SimpleNamespace(asgi_proxy=DummyProxy))
    if "aion.mcp.proxy" in sys.modules:
        return importlib.reload(sys.modules["aion.mcp.proxy"])
    return importlib.import_module("aion.mcp.proxy")


def test_a_configured_port_is_proxied(tmp_path, proxy_mod) -> None:
    """`aion.mcp.port` mounts a proxy to that port on localhost."""
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  agents:\n    a:\n      path: 'm:A'\n  mcp:\n    port: 8080\n")

    proxy = proxy_mod.load_proxy(cfg)

    assert isinstance(proxy, DummyProxy)
    assert proxy.url.endswith(":8080")


def test_no_mcp_section_means_no_proxy(tmp_path, proxy_mod) -> None:
    """A configuration that says nothing about MCP mounts nothing."""
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  agents:\n    a:\n      path: 'm:A'\n")

    assert proxy_mod.load_proxy(cfg) is None


def test_an_mcp_section_without_a_port_means_no_proxy(tmp_path, proxy_mod) -> None:
    """The section may be present and empty; there is still nothing to proxy."""
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  agents:\n    a:\n      path: 'm:A'\n  mcp: {}\n")

    assert proxy_mod.load_proxy(cfg) is None


def test_a_missing_file_means_no_proxy(tmp_path, proxy_mod) -> None:
    """No configuration file at all is not an error here."""
    assert proxy_mod.load_proxy(tmp_path / "nowhere.yaml") is None


def test_a_misspelled_key_fails_instead_of_mounting_nothing(tmp_path, proxy_mod) -> None:
    """A file that exists and is wrong is a startup failure, not a silent no-proxy.

    `prt` used to be read as "no port configured": the proxy did not mount and
    nothing said why. Now the same typo names itself.
    """
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  agents:\n    a:\n      path: 'm:A'\n  mcp:\n    prt: 8080\n")

    with pytest.raises(ConfigurationError) as failure:
        proxy_mod.load_proxy(cfg)

    assert "prt" in str(failure.value.details)
