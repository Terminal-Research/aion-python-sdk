import importlib
import logging
from contextlib import nullcontext
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

    assert created.get("url") == "postgresql+psycopg://example"
    assert created.get("ran")


def test_run_migrations_logs_steps(monkeypatch, caplog):
    """Ensure each migration emits debug logs when executed."""

    monkeypatch.delenv("POSTGRES_URL", raising=False)
    module = importlib.import_module("aion.server.db.migrations.env")
    importlib.reload(module)

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")

    class DummyEngine:
        def connect(self):
            class Conn:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    pass

            return Conn()

    def fake_configure(**kwargs):
        caplog.before = kwargs.get("process_revision_directives")
        caplog.after = kwargs.get("on_version_apply")

    def fake_run_migrations():
        rev = type("Rev", (), {"path": "foo.py"})()
        if caplog.before:
            caplog.before(module.context, rev, [])
        if caplog.after:
            caplog.after(module.context, rev, [])

    monkeypatch.setattr(module, "create_engine", lambda url: DummyEngine())
    monkeypatch.setattr(module.context, "configure", fake_configure)
    monkeypatch.setattr(module.context, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(module.context, "begin_transaction", lambda: nullcontext())

    with caplog.at_level(logging.DEBUG):
        module.run_migrations()

    assert "Running migration foo.py" in caplog.text
    assert "Completed migration foo.py" in caplog.text


def test_upgrade_to_head_sets_cmd_opts(monkeypatch):
    """Ensure ``upgrade_to_head`` populates ``config.cmd_opts``."""

    module = importlib.import_module("aion.server.db.migrations")
    importlib.reload(module)

    monkeypatch.setenv("POSTGRES_URL", "postgresql://example")
    monkeypatch.setattr(module, "test_permissions", lambda url: {"can_create_table": True})

    recorded = {}

    def fake_upgrade(cfg, revision):
        recorded["cmd_opts"] = getattr(cfg, "cmd_opts", None)

    monkeypatch.setattr(module.command, "upgrade", fake_upgrade)

    module.upgrade_to_head()

    assert recorded.get("cmd_opts") is not None
