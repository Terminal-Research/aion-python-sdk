"""Alembic environment for database migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import context
from alembic.config import Config
from sqlalchemy import create_engine

from aion.server.db import get_config

# ``alembic.context`` exposes ``config`` only when executed via Alembic's
# command line utilities. When this module is imported directly (e.g. during
# server startup), the attribute is missing. Create a basic ``Config`` object in
# that case so that configuration options can still be set.
if not hasattr(context, "config"):
    context.config = Config()  # type: ignore[attr-defined]

config = context.config

# Allow running migrations without an alembic.ini
script_location = Path(__file__).parent
config.set_main_option("script_location", str(script_location))

cfg = get_config()
DATABASE_URL = cfg.url if cfg else ""
config.set_main_option("sqlalchemy.url", DATABASE_URL)

engine = create_engine(DATABASE_URL) if DATABASE_URL else None


def run_migrations() -> None:
    """Run Alembic migrations."""
    if engine is None:
        raise RuntimeError("No database configured")

    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():  # pragma: no cover - offline migrations
    context.configure(url=DATABASE_URL)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations()
