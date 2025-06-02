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
    _create_test_table()
    
    try:
        logger.debug("Starting database migrations to head")
        # Try to run the migrations
        command.upgrade(config, "head")
        logger.debug("Database migrations completed successfully")
    except Exception as e:
        logger.error(f"Database migration failed: {e}", exc_info=True)
        raise
    
def _create_test_table():
    """Create tables directly using SQLAlchemy to test if that works.
    
    This bypasses Alembic completely and creates the tables using SQLAlchemy directly.
    If successful, it will also create the alembic_version table with the latest version
    to prevent future migration attempts.
    """
    from sqlalchemy import create_engine, MetaData, Table, Column, String, JSON, DateTime, text
    from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.sql import func
    import uuid
    
    # Get connection URL from Alembic config
    conn_url = str(config.get_main_option("sqlalchemy.url"))
    
    try:
        # Create SQLAlchemy engine
        engine = create_engine(conn_url)
        metadata = MetaData()
        
        # Define tables
        logger.info("Creating test tables directly with SQLAlchemy")
        
        # Create threads table
        threads = Table(
            "threads", metadata,
            Column("id", UUID(), primary_key=True),
            Column("context_id", String(), unique=True, nullable=False),
            Column("artifacts", JSON(), nullable=False),
            Column(
                "created_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
            Column(
                "updated_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
        )
        
        # Create tasks table
        tasks = Table(
            "tasks", metadata,
            Column("id", UUID(), primary_key=True),
            Column("context_id", String(), unique=True, nullable=False),
            Column("task", JSON(), nullable=False),
            Column(
                "created_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
            Column(
                "updated_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=func.now(),
            ),
        )
        
        # Create alembic_version table to track migrations
        alembic_version = Table(
            "alembic_version", metadata,
            Column("version_num", String(32), primary_key=True),
        )
        
        # Create tables
        logger.info("Creating all tables...")
        metadata.create_all(engine)
        
        # Insert the latest migration version to prevent future migration attempts
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('002') ON CONFLICT (version_num) DO NOTHING"))
            conn.commit()
            
        logger.info("Tables created successfully via direct SQLAlchemy approach")
        return True
    except Exception as e:
        logger.error(f"Direct table creation failed: {e}", exc_info=True)
        return False
    
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

