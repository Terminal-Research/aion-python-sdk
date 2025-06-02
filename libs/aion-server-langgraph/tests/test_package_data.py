import importlib
import importlib.resources as resources
import sys
import types
from pathlib import Path
import pytest

pytest.importorskip('alembic')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
src_path = PROJECT_ROOT / 'src'
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Provide dummy psycopg module if missing
if 'psycopg' not in sys.modules:
    sys.modules['psycopg'] = types.ModuleType('psycopg')


def test_migrations_versions_included():
    """Ensure migration scripts are packaged for Alembic."""
    pkg = importlib.import_module('aion.server.db.migrations.versions')
    files = {p.name for p in resources.files(pkg).iterdir()}
    assert '001_create_threads.py' in files
    assert '002_create_tasks.py' in files
