"""Where a database stands against the migrations of this SDK version."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine

from aion.db.postgres.constants import AION_SCHEMA
from .env import config

__all__ = ["SchemaCheck", "SchemaState", "check_sdk_schema"]

SDK_SCHEMA_NAME = "SDK tables"


class SchemaState(str, Enum):
    """How a set of tables compares with what the installed code expects."""

    CURRENT = "current"
    """Every migration the code knows is applied, and nothing newer."""

    BEHIND = "behind"
    """Migrations the code knows are missing; applying them brings it current."""

    AHEAD = "ahead"
    """The database was migrated by a newer version of the code."""


@dataclass(frozen=True)
class SchemaCheck:
    """The state of one set of tables, with a line a person can act on.

    Attributes:
        name: Which tables: the SDK's own, or a framework's.
        state: How they compare with the installed code.
        detail: What was found, such as the revision the database is at.
    """

    name: str
    state: SchemaState
    detail: str


async def check_sdk_schema() -> SchemaCheck:
    """Compare the database's ``alembic_version`` with this SDK's migrations.

    Read-only: it takes no lock and changes nothing.

    Returns:
        ``BEHIND`` when the schema has not been migrated or is at an older
        revision, ``AHEAD`` when it is at a revision this SDK does not know,
        which a newer SDK applied, and ``CURRENT`` otherwise.
    """
    return await asyncio.to_thread(_check)


def _check() -> SchemaCheck:
    script = ScriptDirectory.from_config(config)
    heads = set(script.get_heads())
    engine = create_engine(
        config.get_main_option("sqlalchemy.url"),
        connect_args={"options": f"-csearch_path={AION_SCHEMA}"},
    )
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()

    newest = ", ".join(sorted(heads))
    if not current:
        return SchemaCheck(SDK_SCHEMA_NAME, SchemaState.BEHIND, f"not migrated; the newest revision is {newest}")
    at = ", ".join(sorted(current))
    unknown = sorted(revision for revision in current if not _known(script, revision))
    if unknown:
        return SchemaCheck(
            SDK_SCHEMA_NAME,
            SchemaState.AHEAD,
            f"at revision {at}, which this SDK version does not know; its newest is {newest}",
        )
    if current != heads:
        return SchemaCheck(SDK_SCHEMA_NAME, SchemaState.BEHIND, f"at revision {at}; the newest is {newest}")
    return SchemaCheck(SDK_SCHEMA_NAME, SchemaState.CURRENT, f"at revision {at}")


def _known(script: ScriptDirectory, revision: str) -> bool:
    try:
        return script.get_revision(revision) is not None
    except CommandError:
        return False
