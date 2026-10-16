"""``aion db check``, ``aion db migrate`` and ``aion db strip-request-metadata``: what they report and how they exit."""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from asyncclick.testing import CliRunner

from aion.cli.cli import cli
from aion.db.postgres.migrations import SchemaCheck, SchemaState

db_command = importlib.import_module("aion.cli.commands.db")


@pytest.fixture
def database(monkeypatch):
    """The server's database module, with the database itself left out."""
    from aion.server import database

    manager = Mock()

    @asynccontextmanager
    async def connected(_command):
        yield manager

    monkeypatch.setattr(db_command, "_connected", connected)
    monkeypatch.setattr(database, "check_database", AsyncMock())
    monkeypatch.setattr(database, "migrate_database", AsyncMock())
    return database


@pytest.fixture
def request_metadata(monkeypatch):
    """The request metadata clean-up, with the database itself left out."""
    from aion.server.tasks import request_metadata

    @asynccontextmanager
    async def connected(_command):
        yield Mock()

    monkeypatch.setattr(db_command, "_connected", connected)
    monkeypatch.setattr(request_metadata, "strip_request_metadata", AsyncMock(return_value=[
        request_metadata.RequestMetadataFinding("tasks", rows=2, keys=3),
        request_metadata.RequestMetadataFinding("task_events", rows=4, keys=5),
    ]))
    return request_metadata


def _checks(*states: SchemaState) -> list[SchemaCheck]:
    return [SchemaCheck(f"tables {index}", state, "detail") for index, state in enumerate(states)]


async def test_check_passes_a_current_database(database) -> None:
    database.check_database.return_value = _checks(SchemaState.CURRENT, SchemaState.CURRENT)

    result = await CliRunner().invoke(cli, ["db", "check"])

    assert result.exit_code == 0
    assert "tables 1: current (detail)" in result.output


async def test_check_fails_a_database_behind_and_says_what_to_run(database) -> None:
    database.check_database.return_value = _checks(SchemaState.CURRENT, SchemaState.BEHIND)

    result = await CliRunner().invoke(cli, ["db", "check"])

    assert result.exit_code == 1
    assert "aion db migrate" in result.output


async def test_check_accepts_a_database_newer_than_the_code(database) -> None:
    """Migrations only add, so the older code runs on it."""
    database.check_database.return_value = _checks(SchemaState.AHEAD)

    result = await CliRunner().invoke(cli, ["db", "check"])

    assert result.exit_code == 0
    assert "ahead" in result.output


async def test_migrate_reports_a_failure_with_a_non_zero_exit(database) -> None:
    database.migrate_database.side_effect = PermissionError("cannot create tables")

    result = await CliRunner().invoke(cli, ["db", "migrate"])

    assert result.exit_code == 1
    assert "cannot create tables" in result.output


async def test_strip_request_metadata_only_reports_without_apply(request_metadata) -> None:
    result = await CliRunner().invoke(cli, ["db", "strip-request-metadata"])

    assert result.exit_code == 0
    assert request_metadata.strip_request_metadata.await_args.kwargs == {"apply": False}
    assert "tasks: 3 keys to remove in 2 rows" in result.output
    assert "task_events: 5 keys to remove in 4 rows" in result.output
    assert "run with --apply" in result.output


async def test_strip_request_metadata_removes_with_apply(request_metadata) -> None:
    result = await CliRunner().invoke(cli, ["db", "strip-request-metadata", "--apply"])

    assert result.exit_code == 0
    assert request_metadata.strip_request_metadata.await_args.kwargs == {"apply": True}
    assert "tasks: 3 keys removed in 2 rows" in result.output
    assert "--apply" not in result.output


async def test_without_postgres_url_every_command_refuses(monkeypatch) -> None:
    from aion.db.settings import db_settings

    monkeypatch.setattr(db_settings, "pg_url", None)

    for command in ("check", "migrate", "strip-request-metadata"):
        result = await CliRunner().invoke(cli, ["db", command])
        assert result.exit_code == 1
        assert "POSTGRES_URL is not set" in result.output
