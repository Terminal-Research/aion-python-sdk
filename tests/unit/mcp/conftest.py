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
    # asgi-proxy-lib installs the module ``asgi_proxy``, whose ``asgi_proxy()``
    # is a factory for an ASGI application rather than a class.
    monkeypatch.setitem(
        sys.modules,
        "asgi_proxy",
        types.SimpleNamespace(asgi_proxy=lambda backend: object()),
    )
