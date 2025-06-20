import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
src_path = PROJECT_ROOT / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

if "aion" in sys.modules:
    import pkgutil

    pkg = sys.modules["aion"]
    pkg.__path__ = pkgutil.extend_path(pkg.__path__, pkg.__name__)


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
