"""CLI commands for the PostgreSQL database the agent servers share."""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncclick as click

from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)


@click.group(name="db")
def db() -> None:
    """Apply or check the migrations of the database at POSTGRES_URL.

    A server applies them itself when it starts, unless DB_MIGRATE_ON_START is
    false; then run `aion db migrate` before the servers start.
    """


@db.command(name="migrate")
async def migrate() -> None:
    """Apply every migration: the SDK's tables, then each installed framework's.

    Safe to run while servers start; exits non-zero when a migration fails.
    """
    database = _server_database("aion db migrate")
    async with _connected("aion db migrate") as db_manager:
        try:
            await database.migrate_database(db_manager)
        except Exception as error:
            raise click.ClickException(f"Database migration failed: {error}") from error
    click.echo("Database migrations applied.")


@db.command(name="check")
async def check() -> None:
    """Report whether every migration is applied, without changing anything.

    Exits non-zero when the database is behind this SDK version. A database
    migrated by a newer version is reported and accepted.
    """
    database = _server_database("aion db check")
    from aion.db.postgres.migrations import SchemaState

    async with _connected("aion db check") as db_manager:
        checks = await database.check_database(db_manager)
    for item in checks:
        click.echo(f"{item.name}: {item.state.value} ({item.detail})")
    if any(item.state is SchemaState.BEHIND for item in checks):
        raise click.ClickException("The database is behind this SDK version; run `aion db migrate`.")


def _server_database(command: str):
    """Import the server's database module, which needs the server extra.

    Imported here rather than at module level, as ``aion serve`` does:
    ``aion --help`` has to work in a base install.
    """
    try:
        from aion.server import database
        from aion.server.logging import setup_root_logger
    except MissingOptionalDependency as error:
        raise click.ClickException(str(missing_server_extra_error(command, error))) from error
    except ModuleNotFoundError as error:
        if is_own_module(error.name):
            raise
        raise click.ClickException(str(missing_server_extra_error(command, error))) from error
    setup_root_logger()
    return database


@asynccontextmanager
async def _connected(command: str):
    """Yield a database manager opened on POSTGRES_URL, closed afterwards."""
    from aion.db.postgres import DbFactory, db_manager

    factory = DbFactory(db_manager=db_manager)
    try:
        connected = await factory.initialize()
    except RuntimeError as error:
        raise click.ClickException(str(error)) from error
    if not connected:
        raise click.ClickException(f"POSTGRES_URL is not set; {command} needs the database to work on.")
    try:
        yield db_manager
    finally:
        await factory.cleanup()
