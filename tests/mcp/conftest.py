import sys
import types

import pytest


@pytest.fixture(autouse=True)
def stub_yaml(monkeypatch):
    """Provide a minimal ``yaml`` module for tests."""

    def safe_load(fh):
        text = fh.read()
        port = None
        for line in text.splitlines():
            if "port:" in line:
                try:
                    port = int(line.split("port:")[1].strip())
                except ValueError:
                    port = None
        if port is None:
            return {}
        return {"aion": {"mcp": {"port": port}}}

    monkeypatch.setitem(sys.modules, "yaml", types.SimpleNamespace(safe_load=safe_load))
    monkeypatch.setitem(
        sys.modules,
        "asgi_proxy_lib",
        types.SimpleNamespace(ASGIProxy=type("Proxy", (), {"__init__": lambda self, url: None})),
    )
