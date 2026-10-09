"""The SDK's database schema, migrated and checked in one place.

Every table the SDK keeps in PostgreSQL comes from here: the SDK's own tables
through its Alembic chain (which also carries the tables built on a2a-sdk's
models, see ``docs/development/a2a-sdk-mapping.md``), then the tables of each
installed framework through its plugin - LangGraph's checkpoints, ADK's
sessions. The frameworks are the installed plugins, not the agents'
frameworks: no agent code runs.

``aion db migrate`` and ``aion db check`` call :func:`migrate_database` and
:func:`check_database`. A starting server calls :func:`prepare_database`,
which does one or the other as ``DB_MIGRATE_ON_START`` says. Agent build and
the stores only connect to tables that exist.

Migrations only add. A database migrated by a newer SDK version is reported
``AHEAD`` and left as it is, with a warning, and the server starts: the older
code runs on the newer schema.
"""

from __future__ import annotations

import logging

from aion.core.db import DbManagerProtocol
from aion.core.exceptions import AionError
from aion.db.postgres.migrations import SchemaCheck, SchemaState, check_sdk_schema, upgrade_to_head
from aion.db.settings import db_settings
from aion.server.plugins import discover_installed_plugins

__all__ = [
    "DatabaseBehindError",
    "check_database",
    "migrate_database",
    "prepare_database",
]

logger = logging.getLogger(__name__)


class DatabaseBehindError(AionError):
    """The database lacks migrations this SDK version needs, and the server may not apply them."""


async def migrate_database(db_manager: DbManagerProtocol) -> None:
    """Apply every migration: the SDK's tables, then each installed framework's.

    Safe to run from several servers at once; each step takes its own advisory
    lock. Tables a newer SDK version migrated are skipped with a warning.

    Args:
        db_manager: An initialized database manager.
    """
    await upgrade_to_head()
    for plugin in await discover_installed_plugins():
        check = await plugin.check_database(db_manager)
        if check is not None and check.state is SchemaState.AHEAD:
            _warn_ahead(check)
            continue
        await plugin.migrate_database(db_manager)
        logger.info("Migrated the %s tables", plugin.name())


async def check_database(db_manager: DbManagerProtocol) -> list[SchemaCheck]:
    """Report where every set of tables stands, without changing anything.

    Args:
        db_manager: An initialized database manager.

    Returns:
        The SDK's tables first, then one check per installed framework that
        keeps tables.
    """
    checks = [await check_sdk_schema()]
    for plugin in await discover_installed_plugins():
        check = await plugin.check_database(db_manager)
        if check is not None:
            checks.append(check)
    return checks


async def prepare_database(db_manager: DbManagerProtocol) -> None:
    """Make the database ready for a starting server, as ``DB_MIGRATE_ON_START`` says.

    With ``DB_MIGRATE_ON_START`` on (the default) the server migrates, as
    ``aion db migrate`` does. With it off the server only checks, and the
    database user needs no right to change the schema.

    Args:
        db_manager: An initialized database manager.

    Raises:
        DatabaseBehindError: Migrations are off and the database lacks some.
    """
    if db_settings.migrate_on_start:
        await migrate_database(db_manager)
        logger.info("Database migrations completed successfully")
        return

    checks = await check_database(db_manager)
    behind = [check for check in checks if check.state is SchemaState.BEHIND]
    for check in checks:
        if check.state is SchemaState.AHEAD:
            _warn_ahead(check)
    if behind:
        details = "; ".join(f"{check.name}: {check.detail}" for check in behind)
        raise DatabaseBehindError(
            f"The database is not migrated ({details}). DB_MIGRATE_ON_START is false, so run "
            "`aion db migrate` before starting the server."
        )
    logger.info("Database migrations are applied")


def _warn_ahead(check: SchemaCheck) -> None:
    logger.warning(
        "The %s are newer than this SDK version (%s); they are left as they are",
        check.name,
        check.detail,
    )
