"""Tests for aion.core.settings.ApiSettings — URL computation logic.

The pydantic-settings env-loading machinery is not under test here; we test the
pure URL construction properties which are the only non-trivial logic in this class.
"""

import pytest
from pydantic import ValidationError

from aion.core.settings import ApiSettings


def _api(host: str) -> ApiSettings:
    return ApiSettings(AION_API_HOST=host)


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
