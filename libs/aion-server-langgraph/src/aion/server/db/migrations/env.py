"""Alembic environment for database migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import context
from alembic.config import Config
from sqlalchemy import create_engine
from alembic.runtime.migration import MigrationContext, MigrationInfo
import logging

logger = logging.getLogger(__name__)

from aion.server.db import get_config, sqlalchemy_url

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
logger.debug("Using SQLAlchemy database URL: %s", DATABASE_URL)
config.set_main_option("sqlalchemy.url", sqlalchemy_url(DATABASE_URL))


def _get_engine() -> "Engine":
    """Return a SQLAlchemy engine for the configured database."""
    cfg = get_config()
    if not cfg:
        raise RuntimeError("No database configured")

    url = sqlalchemy_url(cfg.url)
    config.set_main_option("sqlalchemy.url", url)
    return create_engine(url)


def _log_revision_start(
    context: "MigrationContext", revision: object, directives: list
) -> None:
    """Log which migration is about to run."""
    path = getattr(revision, "path", str(revision))
    logger.debug("Running migration %s", path)


def _log_revision_end(
    ctx: "MigrationContext", step: "MigrationInfo", heads: list
) -> None:
    """Log that a migration finished successfully."""
    logger.debug("Completed migration %s", step.up_revision_id)


def run_migrations() -> None:
    """Run Alembic migrations."""
    engine = _get_engine()

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            process_revision_directives=_log_revision_start,
            on_version_apply=_log_revision_end,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_offline_migrations() -> None:
    """Run migrations in offline mode."""

    cfg = get_config()
    url = sqlalchemy_url(cfg.url) if cfg else ""
    context.configure(
        url=url,
        process_revision_directives=_log_revision_start,
        on_version_apply=_log_revision_end,
    )
    with context.begin_transaction():
        context.run_migrations()


# When executed via Alembic's command helpers, ``context.config`` will include
# ``cmd_opts``. Only in that case should this module trigger migrations
# automatically. Importing this module (e.g. during application startup) should
# not run migrations on import.
if getattr(config, "cmd_opts", None):  # pragma: no cover - CLI invocation
    if context.is_offline_mode():
        run_offline_migrations()
    else:
        run_migrations()
