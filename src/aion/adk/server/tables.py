"""The tables the SDK keeps for ADK in the ADK schema, beside google-adk's own.

google-adk creates and versions its session tables itself. What the SDK adds
for an ADK agent - the memory the server gives it (``memory``), the version
numbers its artifact service gives (``artifacts.versions``) - lies in the
same schema, so a database without ADK installed has none of it, and comes
from the migrations here: a list of statements that only add, applied in
order and recorded one row per statement in ``aion_migrations``, as
LangGraph records its own in ``checkpoint_migrations``. They run with the
ADK plugin's migrations, after google-adk's tables, under the same lock.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aion.core.db import DbManagerProtocol
from aion.db.postgres.migrations import SchemaCheck, SchemaState

from aion.db.postgres.constants import AION_SCHEMA

from .artifacts.versions import ARTIFACT_VERSIONS_TABLE
from .constants import AION_ADK_SCHEMA

__all__ = ["AION_TABLES_NAME", "MEMORY_TABLE", "MIGRATIONS", "check_aion_tables", "migrate_aion_tables"]

AION_TABLES_NAME = "SDK tables for ADK"

MEMORY_TABLE = "memory_entries"
"""One row per memory: the text of one event, or one entry an agent added, of one agent and owner."""

MIGRATIONS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {{schema}}.{MEMORY_TABLE} (
        app_name text NOT NULL,
        user_id text NOT NULL,
        session_id text NOT NULL,
        entry_id text NOT NULL,
        author text,
        "timestamp" text,
        content jsonb NOT NULL,
        custom_metadata jsonb NOT NULL DEFAULT '{{{{}}}}'::jsonb,
        text text NOT NULL,
        search tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text)) STORED,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (app_name, user_id, session_id, entry_id)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_search_idx ON {{schema}}.{MEMORY_TABLE} USING gin (search)",
    f"""
    CREATE TABLE IF NOT EXISTS {{schema}}.{ARTIFACT_VERSIONS_TABLE} (
        agent_id text NOT NULL,
        owner_scope text NOT NULL,
        context_id text NOT NULL,
        artifact_name text NOT NULL,
        version integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (agent_id, owner_scope, context_id, artifact_name, version)
    )
    """,
    # The numbers given while the table was in the SDK's own schema, so none
    # of them is given again. TODO(drop-aion-adk-artifact-versions)
    f"""
    DO $copy$
    BEGIN
        IF to_regclass('{AION_SCHEMA}.{ARTIFACT_VERSIONS_TABLE}') IS NOT NULL THEN
            INSERT INTO {{schema}}.{ARTIFACT_VERSIONS_TABLE}
                (agent_id, owner_scope, context_id, artifact_name, version, created_at)
            SELECT agent_id, owner_scope, context_id, artifact_name, version, created_at
            FROM "{AION_SCHEMA}".{ARTIFACT_VERSIONS_TABLE}
            ON CONFLICT DO NOTHING;
        END IF;
    END
    $copy$
    """,
)
"""Every migration of the SDK's ADK tables, in order. Only ever appended to, and each one only adds."""

_MIGRATIONS_TABLE = "aion_migrations"


def _quoted(schema: str) -> str:
    return '"' + schema.replace('"', '""') + '"'


async def migrate_aion_tables(db_manager: DbManagerProtocol, schema: str = AION_ADK_SCHEMA) -> None:
    """Apply the migrations the schema has not had yet, under the ADK setup lock."""
    from .session.backends.postgres import SESSION_SETUP_LOCK_KEY

    engine: AsyncEngine = db_manager.get_engine()
    async with engine.connect() as lock_conn:
        await lock_conn.execute(text("SELECT pg_advisory_lock(CAST(:key AS BIGINT))"), {"key": SESSION_SETUP_LOCK_KEY})
        await lock_conn.commit()
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quoted(schema)}"))
                await conn.execute(
                    text(f"CREATE TABLE IF NOT EXISTS {_quoted(schema)}.{_MIGRATIONS_TABLE} (v integer PRIMARY KEY)")
                )
                applied = await _applied(conn, schema)
                for version, statement in enumerate(MIGRATIONS):
                    if version <= applied:
                        continue
                    await conn.execute(text(statement.format(schema=_quoted(schema))))
                    await conn.execute(
                        text(f"INSERT INTO {_quoted(schema)}.{_MIGRATIONS_TABLE} (v) VALUES (:v)"), {"v": version}
                    )
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(CAST(:key AS BIGINT))"), {"key": SESSION_SETUP_LOCK_KEY}
            )
            await lock_conn.commit()


async def check_aion_tables(db_manager: DbManagerProtocol, schema: str = AION_ADK_SCHEMA) -> SchemaCheck:
    """Compare the applied migrations with the ones this SDK version knows. Read-only."""
    latest = len(MIGRATIONS) - 1
    async with db_manager.get_engine().connect() as conn:
        exists = (
            await conn.execute(text("SELECT to_regclass(:name)"), {"name": f"{_quoted(schema)}.{_MIGRATIONS_TABLE}"})
        ).scalar()
        if exists is None:
            return SchemaCheck(AION_TABLES_NAME, SchemaState.BEHIND, "not migrated")
        applied = await _applied(conn, schema)
    detail = f"migration {applied} applied; this SDK version's newest is {latest}"
    if applied > latest:
        return SchemaCheck(AION_TABLES_NAME, SchemaState.AHEAD, detail)
    if applied < latest:
        return SchemaCheck(AION_TABLES_NAME, SchemaState.BEHIND, detail)
    return SchemaCheck(AION_TABLES_NAME, SchemaState.CURRENT, detail)


async def _applied(conn: AsyncConnection, schema: str) -> int:
    value = (await conn.execute(text(f"SELECT max(v) FROM {_quoted(schema)}.{_MIGRATIONS_TABLE}"))).scalar()
    return -1 if value is None else int(value)
