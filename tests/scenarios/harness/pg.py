"""PostgreSQL for the persistence and distributed variants.

The scenarios never start a database. ``POSTGRES_TEST_URL`` names one that
may be wiped - the Makefile supplies it the same way the SDK's own
integration tests do - and these variants pass it to the server as
``POSTGRES_URL``, which is what turns the in-memory task store into a durable
one and the server into an instance of a2a-sdk's cluster mode.

One thing a scenario cannot see from outside is the version a2a-sdk keeps
for a task: no A2A response carries it. ``task_version`` is the single,
deliberate exception - a read-only ``SELECT`` for one task id, never a write
and never a count of the whole table, which over a shared database would be
measuring other scenarios' tasks.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from aion.db.postgres.constants import AION_SCHEMA, TASK_VERSIONS_TABLE

__all__ = [
    "postgres_url",
    "postgres_env",
    "have_postgres",
    "task_version",
    "empty_database",
    "user_without_schema_rights",
]

POSTGRES_TEST_URL_VAR = "POSTGRES_TEST_URL"


def postgres_url() -> Optional[str]:
    """The database the persistence scenarios may use, or None."""
    return os.environ.get(POSTGRES_TEST_URL_VAR) or None


def have_postgres() -> bool:
    """Whether a database was named for this run."""
    return postgres_url() is not None


def postgres_env() -> dict[str, str]:
    """Environment that puts the server on that database."""
    url = postgres_url()
    if url is None:
        raise RuntimeError(
            f"{POSTGRES_TEST_URL_VAR} is not set; run the persistence scenarios via "
            "`make tests-scenarios-persistence`."
        )
    return {"POSTGRES_URL": url}


async def task_version(task_id: str) -> Optional[int]:
    """The version a2a-sdk's versioned store holds for this task, or None.

    A short-lived connection of its own rather than the server's pool: the
    scenarios run outside the processes under test and must not share
    anything with them.
    """
    async with await psycopg.AsyncConnection.connect(postgres_url()) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT version FROM {AION_SCHEMA}.{TASK_VERSIONS_TABLE} WHERE task_id = %s",
                (task_id,),
            )
            row = await cursor.fetchone()
            return None if row is None else row[0]



@contextmanager
def empty_database() -> Iterator[str]:
    """A database of its own on the test server, created empty and dropped afterwards.

    For the scenarios that start from nothing - the migrations themselves -
    and so cannot use the database the rest of the group shares.

    Yields:
        The URL of the new database.
    """
    url = postgres_url()
    name = f"aion_scenario_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield _url_with(url, database=name)
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@contextmanager
def user_without_schema_rights(database_url: str) -> Iterator[str]:
    """A login that may read and write the migrated tables and change nothing else.

    It can neither create a schema in the database nor a table in any schema
    the SDK uses: what a deployment gives its servers when it migrates
    beforehand with ``DB_MIGRATE_ON_START=false``.

    Args:
        database_url: A database already migrated.

    Yields:
        The URL of that database as the restricted user.
    """
    name = f"aion_server_{uuid.uuid4().hex[:12]}"
    password = uuid.uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as connection:
        schemas = [
            row[0]
            for row in connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'aion%'"
            ).fetchall()
        ]
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(name), sql.Literal(password))
        )
        for schema in schemas:
            for statement in (
                "GRANT USAGE ON SCHEMA {schema} TO {role}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
            ):
                connection.execute(
                    sql.SQL(statement).format(schema=sql.Identifier(schema), role=sql.Identifier(name))
                )
    try:
        yield _url_with(database_url, user=name, password=password)
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def _url_with(
    url: str,
    *,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """``url`` with another database or login, still a ``postgresql://`` URL as the SDK requires."""
    parts = urlsplit(url)
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{quote(user, safe='')}:{quote(password or '', safe='')}@{host}"
    path = f"/{database}" if database is not None else parts.path
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))
