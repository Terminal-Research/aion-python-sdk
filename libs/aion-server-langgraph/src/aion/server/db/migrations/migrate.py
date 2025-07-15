"""Alembic migration utilities for the Aion server."""

from __future__ import annotations

import asyncio
import logging

from alembic import command

from .env import config
from .utils import (
    fail_if_no_permissions,
    log_migrations,
    setup_checkpointer_tables,
)

logger = logging.getLogger(__name__)


async def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision."""

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

        # Try to run the migrations
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, command.upgrade, config, "head")
        logger.debug("Database migrations completed successfully")

        # Setup checkpointer tables after main migrations
        await setup_checkpointer_tables()
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise
