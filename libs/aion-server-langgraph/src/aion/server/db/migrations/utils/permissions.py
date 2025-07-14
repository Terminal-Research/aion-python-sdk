from __future__ import annotations

import logging
import sys

from ..env import config
from aion.server.db import test_permissions, psycopg_url

logger = logging.getLogger(__name__)


async def fail_if_no_permissions():
    """Test database permissions and fail if insufficient for migrations.

    This function checks if the current database user has permissions to create tables.
    If not, it logs an error and exits the process.
    """
    # Get connection URL from Alembic config
    conn_url = psycopg_url(config.get_main_option("sqlalchemy.url"))

    # Check permissions
    logger.debug("Testing database permissions before migrations")
    permissions = await test_permissions(conn_url)

    if not permissions['can_connect']:
        logger.error("Cannot connect to database")
        if permissions['error']:
            logger.error(f"Connection error: {permissions['error']}")
        sys.exit(1)

    if not permissions['can_create_table']:
        error_msg = "Insufficient database permissions to create tables"
        if 'table_error' in permissions:
            error_msg += f": {permissions['table_error']}"
        logger.error(error_msg)
        logger.error("Database migrations cannot proceed without table creation permissions")
        logger.error(
            f"Current user: {permissions['user_info']['current_user'] if permissions['user_info'] else 'Unknown'}")
        logger.error(f"Current database: {permissions['current_database']}")
        sys.exit(1)

    # Schema permissions are not critical, just log a warning
    if not permissions['can_create_schema']:
        logger.warning("User cannot create schemas - this may limit some migration capabilities")
