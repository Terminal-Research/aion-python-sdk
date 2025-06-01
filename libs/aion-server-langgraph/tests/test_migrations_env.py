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
