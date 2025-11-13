"""Alembic environment for database migrations."""

from __future__ import annotations

from pathlib import Path

from aion.shared.logging import get_logger
from alembic import context
from alembic.config import Config
from sqlalchemy import create_engine

logger = get_logger()

from aion.shared.settings import db_settings

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

config.set_main_option("sqlalchemy.url", db_settings.pg_sqlalchemy_url or "")


def _get_engine() -> "Engine":
    """Return a SQLAlchemy engine for the configured database."""
    url = db_settings.pg_sqlalchemy_url or ""
    if not url:
        raise RuntimeError("No database configured")

    config.set_main_option("sqlalchemy.url", url)
    return create_engine(url)


def _log_revision_start(
        context: "MigrationContext", revision: object, directives: list
) -> None:
    """Log which migration is about to run."""
    path = getattr(revision, "path", str(revision))
    logger.debug("Running migration %s", path)


def _log_revision_end(
        ctx: "MigrationContext", step: "MigrationInfo", heads: list, run_args: dict
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

    context.configure(
        url=db_settings.pg_sqlalchemy_url or "",
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
