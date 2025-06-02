"""Utilities for applying database migrations programmatically."""

from __future__ import annotations

from alembic import command

from .env import config

__all__ = ["upgrade_to_head"]


def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Starting database migrations to head")
        # See what versions Alembic thinks are available
        from alembic import script
        script_directory = script.ScriptDirectory.from_config(config)
        revisions = list(script_directory.walk_revisions())
        logger.info(f"Available revisions: {[rev.revision for rev in revisions]}")
        
        # Try to run the migrations
        command.upgrade(config, "head")
        logger.info("Database migrations completed successfully")
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise
