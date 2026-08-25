"""Alembic migration utilities for the Aion server."""

from __future__ import annotations
import logging

import asyncio

from alembic import command

from .env import config
from .utils import (
    fail_if_no_permissions,
    log_migrations
)

logger = logging.getLogger(__name__)


async def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision.

    This method is idempotent and safe to call from multiple agents simultaneously.
    The migration environment serializes concurrent runners with a transaction-level
    advisory lock on the migration connection.

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
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise


def _run_migrations() -> None:
    """Run Alembic migrations to upgrade database schema.

    Concurrent runners are serialized by ``env.run_migrations``. Any migration
    failure is propagated so callers cannot mistake a partial schema for success.
    """
    command.upgrade(config, "head")
