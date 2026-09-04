"""Tests for aion.db.settings.DatabaseSettings — URL parsing logic."""

import pytest
from pydantic import ValidationError

from aion.db.settings import DatabaseSettings


def _db(url: str | None) -> DatabaseSettings:
    return DatabaseSettings(POSTGRES_URL=url)


class TestDatabaseSettingsUrlParsing:
    def test_none_url_is_not_valid(self):
        """Verify that none URL is not valid."""
        db = _db(None)
        assert db.is_valid_pg_url() is False

    def test_none_url_properties_return_none(self):
        """Verify that none URL properties return none."""
        db = _db(None)
        assert db.pg_host is None
        assert db.pg_port is None
        assert db.pg_user_name is None
        assert db.pg_user_password is None
        assert db.pg_db_name is None

    def test_valid_url_parsed_correctly(self):
        """Verify that valid URL parsed correctly."""
        db = _db("postgresql://alice:secret@db.host:5432/mydb")
        assert db.pg_host == "db.host"
        assert db.pg_port == 5432
        assert db.pg_user_name == "alice"
        assert db.pg_user_password == "secret"
        assert db.pg_db_name == "mydb"

    def test_non_postgresql_scheme_raises_validation_error(self):
        """Verify that non postgresql scheme raises validation error."""
        with pytest.raises(ValidationError):
            DatabaseSettings(POSTGRES_URL="mysql://user:pass@host/db")

    def test_url_without_port_returns_none_for_pg_port(self):
        """Verify that URL without port returns none for PostgreSQL port."""
        db = _db("postgresql://user:pass@host/db")
        assert db.pg_port is None
        assert db.pg_host == "host"

    def test_url_without_credentials_returns_none(self):
        """Verify that URL without credentials returns none."""
        db = _db("postgresql://host:5432/db")
        assert db.pg_user_name is None
        assert db.pg_user_password is None
