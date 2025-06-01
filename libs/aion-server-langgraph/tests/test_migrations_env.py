import importlib
import pytest

pytest.importorskip("alembic")
import alembic.context


def test_import_env_without_alembic_config(monkeypatch):
    """Ensure migrations env can load when context lacks ``config``."""
    monkeypatch.delattr(alembic.context, "config", raising=False)

    module = importlib.import_module("aion.server.db.migrations.env")
    importlib.reload(module)

    assert hasattr(alembic.context, "config")


def test_run_migrations_uses_runtime_env(monkeypatch):
    """Ensure migrations use the current ``POSTGRES_URL`` when called."""

    monkeypatch.delenv("POSTGRES_URL", raising=False)
    module = importlib.import_module("aion.server.db.migrations.env")
    importlib.reload(module)

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")

    created = {}

    class DummyEngine:
        def connect(self):
            class Conn:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    pass

            created["connected"] = True
            return Conn()

    monkeypatch.setattr(module, "create_engine", lambda url: created.setdefault("url", url) or DummyEngine())
    monkeypatch.setattr(module.context, "run_migrations", lambda: created.setdefault("ran", True))

    module.run_migrations()

    assert created.get("url") == "postgresql://example"
    assert created.get("ran")
