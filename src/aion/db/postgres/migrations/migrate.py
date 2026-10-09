"""Alembic migration utilities for the Aion server."""

from __future__ import annotations
import logging

import asyncio

from alembic import command

from .env import config
from .status import SchemaState, check_sdk_schema
from .utils import (
    fail_if_no_permissions,
    log_migrations
)

logger = logging.getLogger(__name__)


async def upgrade_to_head() -> None:
    """Upgrade the SDK's tables to the newest revision this SDK version knows.

    Safe to call from several servers at once. The permission check and the
    migrations each take ``MIGRATION_ADVISORY_LOCK_KEY`` as a session-level
    advisory lock (see ``env.run_migrations``). Session-level rather than
    transaction-level, because ``CREATE SCHEMA IF NOT EXISTS`` runs before
    Alembic opens its transaction and is racy on its own: a transaction lock
    would leave it uncovered.

    A database migrated by a newer SDK version is at a revision this one does
    not know. It is left as it is, with a warning: migrations only add, so the
    older code runs on the newer schema.

    Raises:
        RuntimeError: The database cannot be reached.
        PermissionError: The database user cannot create tables.
        Exception: A migration failed; a partial schema is never reported as
            success.
    """
    await fail_if_no_permissions()
    status = await check_sdk_schema()
    if status.state is SchemaState.AHEAD:
        logger.warning(
            "The database is newer than this SDK version (%s); its migrations are skipped",
            status.detail,
        )
        return
    log_migrations()

    try:
        logger.debug("Starting database migrations to head")
        # ``alembic.command`` only runs migrations when ``config.cmd_opts`` is
        # present. When invoked programmatically this attribute is missing, so
        # ensure it exists before calling ``upgrade``.
        if not getattr(config, "cmd_opts", None):
            from types import SimpleNamespace

            config.cmd_opts = SimpleNamespace()

        await asyncio.to_thread(_run_migrations)
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise


def _run_migrations() -> None:
    """Run Alembic migrations to upgrade database schema.

    Concurrent runners are serialized by ``env.run_migrations``. Any migration
    failure is propagated so callers cannot mistake a partial schema for success.
    """
    command.upgrade(config, "head")
