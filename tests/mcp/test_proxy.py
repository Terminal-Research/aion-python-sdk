"""Tests for the aion-mcp proxy loader."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

def reload_module() -> types.ModuleType:
    """Import or reload the proxy module after monkeypatching dependencies."""
    if "aion.mcp.proxy" in sys.modules:
        return importlib.reload(sys.modules["aion.mcp.proxy"])
    return importlib.import_module("aion.mcp.proxy")


def test_load_proxy_returns_none_when_unconfigured(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  graph:\n    g: mod\n")
    proxy_mod = reload_module()
    assert proxy_mod.load_proxy(cfg) is None


def test_load_proxy_returns_proxy_when_configured(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("aion:\n  mcp:\n    port: 8080\n")

    class DummyProxy:
        def __init__(self, url: str) -> None:
            self.url = url

    dummy_mod = types.SimpleNamespace(ASGIProxy=DummyProxy)
    monkeypatch.setitem(sys.modules, "asgi_proxy_lib", dummy_mod)

    proxy_mod = reload_module()

    proxy = proxy_mod.load_proxy(cfg)
    assert isinstance(proxy, DummyProxy)
    assert proxy.url.endswith(":8080")


def test_load_proxy_uses_pyyaml_when_available(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "aion.yaml"
    cfg.write_text("ignored: true\n")

    class DummyProxy:
        def __init__(self, url: str) -> None:
            self.url = url

    def safe_load(fh):
        fh.read()
        return {"aion": {"mcp": {"port": 9000}}}

    dummy_yaml = types.SimpleNamespace(safe_load=safe_load)
    monkeypatch.setitem(sys.modules, "yaml", dummy_yaml)
    monkeypatch.setitem(sys.modules, "asgi_proxy_lib", types.SimpleNamespace(ASGIProxy=DummyProxy))

    proxy_mod = reload_module()

    proxy = proxy_mod.load_proxy(cfg)
    assert isinstance(proxy, DummyProxy)
    assert proxy.url.endswith(":9000")

