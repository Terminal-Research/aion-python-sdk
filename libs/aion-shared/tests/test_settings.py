"""Tests for settings.py — URL computation logic in ApiSettings and DatabaseSettings.

The pydantic-settings env-loading machinery is not under test here; we test the
pure URL construction properties which are the only non-trivial logic in these classes.
"""

import pytest
from pydantic import ValidationError

from aion.shared.settings import ApiSettings, AppSettings, DatabaseSettings


def _api(host: str) -> ApiSettings:
    return ApiSettings(AION_API_HOST=host)


def _db(url: str | None) -> DatabaseSettings:
    return DatabaseSettings(POSTGRES_URL=url)


class TestApiSettingsUrlConstruction:
    def test_https_standard_port_omitted_from_http_url(self):
        """Verify that HTTPS standard port omitted from HTTP URL."""
        s = _api("https://api.example.com")
        assert s.http_url == "https://api.example.com"

    def test_http_standard_port_omitted_from_http_url(self):
        """Verify that HTTP standard port omitted from HTTP URL."""
        s = _api("http://api.example.com")
        assert s.http_url == "http://api.example.com"

    def test_non_standard_port_included_in_http_url(self):
        """Verify that non standard port included in HTTP URL."""
        s = _api("https://api.example.com:8443")
        assert s.http_url == "https://api.example.com:8443"

    def test_gql_url_appends_path(self):
        """Verify that GraphQL URL appends path."""
        s = _api("https://api.example.com")
        assert s.gql_url == "https://api.example.com/api/graphql"

    def test_gql_url_with_custom_port(self):
        """Verify that GraphQL URL with custom port."""
        s = _api("https://api.example.com:8443")
        assert s.gql_url == "https://api.example.com:8443/api/graphql"

    def test_ws_gql_url_uses_wss_for_https(self):
        """Verify that WebSocket GraphQL URL uses WSS for HTTPS."""
        s = _api("https://api.example.com")
        assert s.ws_gql_url.startswith("wss://")
        assert s.ws_gql_url == "wss://api.example.com/ws/graphql"

    def test_ws_gql_url_uses_ws_for_http(self):
        """Verify that WebSocket GraphQL URL uses WebSocket for HTTP."""
        s = _api("http://api.example.com")
        assert s.ws_gql_url.startswith("ws://")
        assert s.ws_gql_url == "ws://api.example.com/ws/graphql"

    def test_ws_gql_url_preserves_non_standard_port(self):
        """Verify that WebSocket GraphQL URL preserves non standard port."""
        s = _api("https://api.example.com:8443")
        assert s.ws_gql_url == "wss://api.example.com:8443/ws/graphql"

    def test_url_caching_returns_same_string(self):
        """Verify that URL caching returns same string."""
        s = _api("https://api.example.com")
        # Access twice — must be identical (cached)
        first = s.http_url
        second = s.http_url
        assert first is second

    def test_trailing_slash_stripped_from_host(self):
        """Verify that trailing slash stripped from host."""
        s = _api("https://api.example.com/")
        assert not s.http_url.endswith("/")

    def test_invalid_host_missing_scheme_raises(self):
        """Verify that invalid host missing scheme raises."""
        with pytest.raises(ValidationError):
            ApiSettings(AION_API_HOST="api.example.com")


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


class TestAppSettings:
    def test_logstash_not_configured_when_both_missing(self):
        """Verify that Logstash not configured when both missing."""
        s = AppSettings(LOGSTASH_HOST=None, LOGSTASH_PORT=None)
        assert s.is_logstash_configured is False

    def test_logstash_not_configured_when_only_host_set(self):
        """Verify that Logstash not configured when only host set."""
        s = AppSettings(LOGSTASH_HOST="logs.host", LOGSTASH_PORT=None)
        assert s.is_logstash_configured is False

    def test_logstash_not_configured_when_only_port_set(self):
        """Verify that Logstash not configured when only port set."""
        s = AppSettings(LOGSTASH_HOST=None, LOGSTASH_PORT=5044)
        assert s.is_logstash_configured is False

    def test_logstash_configured_when_both_set(self):
        """Verify that Logstash configured when both set."""
        s = AppSettings(LOGSTASH_HOST="logs.host", LOGSTASH_PORT=5044)
        assert s.is_logstash_configured is True
