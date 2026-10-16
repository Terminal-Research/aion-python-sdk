"""CLI commands for the PostgreSQL database the agent servers share."""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager

import asyncclick as click

from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)


@click.group(name="db")
def db() -> None:
    """Apply or check the migrations of the database at POSTGRES_URL, or clean its data.

    A server applies the migrations itself when it starts, unless
    DB_MIGRATE_ON_START is false; then run `aion db migrate` before the servers
    start.
    """


@db.command(name="migrate")
async def migrate() -> None:
    """Apply every migration: the SDK's tables, then each installed framework's.

    Safe to run while servers start; exits non-zero when a migration fails.
    """
    database = _server_module("aion db migrate", "aion.server.database")
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
    database = _server_module("aion db check", "aion.server.database")
    from aion.db.postgres.migrations import SchemaState

    async with _connected("aion db check") as db_manager:
        checks = await database.check_database(db_manager)
    for item in checks:
        click.echo(f"{item.name}: {item.state.value} ({item.detail})")
    if any(item.state is SchemaState.BEHIND for item in checks):
        raise click.ClickException("The database is behind this SDK version; run `aion db migrate`.")


@db.command(name="strip-request-metadata")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Remove the keys. Without it the command only reports what it would remove.",
)
async def strip_request_metadata(apply_changes: bool) -> None:
    """Remove inbound extension payloads from stored tasks and the task journal.

    A task's metadata is the server's; the payloads a request carries in its
    metadata - daemon, distribution, cron, traceability - belong to the
    request. This finds them in `tasks` and in the task snapshots of
    `task_events` and, with --apply, removes them; every other key stays.
    Without --apply nothing is changed.
    """
    command = "aion db strip-request-metadata"
    request_metadata = _server_module(command, "aion.server.tasks.request_metadata")
    keys = request_metadata.request_metadata_keys()
    async with _connected(command) as db_manager:
        findings = await request_metadata.strip_request_metadata(db_manager, apply=apply_changes)
    click.echo(f"Request metadata keys: {', '.join(keys)}")
    for finding in findings:
        action = "removed" if apply_changes else "to remove"
        click.echo(f"{finding.table}: {finding.keys} keys {action} in {finding.rows} rows")
    if not apply_changes and any(finding.rows for finding in findings):
        click.echo("Nothing was changed; run with --apply to remove them.")


def _server_module(command: str, name: str):
    """Import a module of the server, which needs the server extra.

    Imported here rather than at module level, as ``aion serve`` does:
    ``aion --help`` has to work in a base install.
    """
    try:
        module = importlib.import_module(name)
        from aion.server.logging import setup_root_logger
    except MissingOptionalDependency as error:
        raise click.ClickException(str(missing_server_extra_error(command, error))) from error
    except ModuleNotFoundError as error:
        if is_own_module(error.name):
            raise
        raise click.ClickException(str(missing_server_extra_error(command, error))) from error
    setup_root_logger()
    return module


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
