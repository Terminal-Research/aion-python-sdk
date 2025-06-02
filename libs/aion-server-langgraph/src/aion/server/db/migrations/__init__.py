"""Utilities for applying database migrations programmatically."""

from __future__ import annotations

import os
import sys
from alembic import command

from .env import config
from aion.server.db import test_permissions

__all__ = ["upgrade_to_head"]

import logging
logger = logging.getLogger(__name__)

def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision."""
    _fail_if_no_permissions()
    _log_migrations()
    
    try:
        logger.debug("Starting database migrations to head")
        # ``alembic.command`` only runs migrations when ``config.cmd_opts`` is
        # present. When invoked programmatically this attribute is missing, so
        # ensure it exists before calling ``upgrade``.
        if not getattr(config, "cmd_opts", None):
            from types import SimpleNamespace

            config.cmd_opts = SimpleNamespace()

        # Try to run the migrations
        command.upgrade(config, "head")
        logger.debug("Database migrations completed successfully")
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise
    
def _log_migrations():
    # See what versions Alembic thinks are available
    from alembic import script
    script_directory = script.ScriptDirectory.from_config(config)
    revisions = list(script_directory.walk_revisions())
    logger.debug(f"Available revisions: {[rev.revision for rev in revisions]}")
        
def _fail_if_no_permissions():
    # Test database permissions
    from sqlalchemy import create_engine, URL
    from sqlalchemy.engine.url import make_url
    conn_url = str(config.get_main_option("sqlalchemy.url"))
    
    # Check permissions before attempting migrations
    logger.debug(f"Testing database permissions before migrations")
    permissions = test_permissions(conn_url)
    
    if not permissions['can_create_table']:
        error_msg = "Insufficient database permissions to create tables"
        if 'table_error' in permissions:
            error_msg += f": {permissions['table_error']}"
        logger.error(error_msg)
        logger.error("Database migrations cannot proceed without table creation permissions")
        logger.error(f"Current user: {permissions['user_info']['current_user'] if permissions['user_info'] else 'Unknown'}")
        logger.error(f"Current database: {permissions['current_database']}")
        sys.exit(1)

