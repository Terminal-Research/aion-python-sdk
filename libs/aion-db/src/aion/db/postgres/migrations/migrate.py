"""Alembic migration utilities for the Aion server."""

from __future__ import annotations

import asyncio

from aion.shared.logging import get_logger
from alembic import command

from .env import config
from .utils import (
    fail_if_no_permissions,
    log_migrations
)

logger = get_logger()


async def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision.

    This method is idempotent and safe to call from multiple agents simultaneously.
    When agents start in parallel, they may race to apply migrations. UniqueViolation
    errors are expected and ignored in this scenario.

    TODO: Infrastructure setup (migrations, DB init) should be moved to a higher level
    and run once before agent startup (e.g., init container in K8s, separate CLI command/execution).
    Agents shouldn't be responsible for database migrations - this improves startup time
    and simplifies the agent lifecycle.
    """

    await fail_if_no_permissions()
    log_migrations()

    try:
        logger.debug("Starting database migrations to head")
        # ``alembic.command`` only runs migrations when ``config.cmd_opts`` is
        # present. When invoked programmatically this attribute is missing, so
        # ensure it exists before calling ``upgrade``.
        if not getattr(config, "cmd_opts", None):
            from types import SimpleNamespace

            config.cmd_opts = SimpleNamespace()

        # Run migrations
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_migrations)
        logger.debug("Database migrations completed successfully")
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise


def _run_migrations() -> None:
    """Run Alembic migrations to upgrade database schema.

    When multiple agents start simultaneously, they may race to apply migrations.
    UniqueViolation errors indicate that another agent has already created the
    database object, which is expected and safe to ignore.
    """
    import psycopg.errors
    from sqlalchemy.exc import IntegrityError

    try:
        command.upgrade(config, "head")
    except IntegrityError as e:
        # SQLAlchemy wraps psycopg errors in IntegrityError
        # Check if the underlying cause is UniqueViolation
        if isinstance(e.orig, psycopg.errors.UniqueViolation):
            # This happens when multiple agents start simultaneously and race
            # to apply migrations. The error indicates another agent already
            # created the database object (table, index, constraint, etc.).
            logger.debug(f"Migration object already exists (parallel startup): {e.orig}")
        else:
            raise
