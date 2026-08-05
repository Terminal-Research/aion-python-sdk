"""Tests for the Logstash endpoint resolver.

Everything about the destination is derived from LOGSTASH_HOST, so these
tests pin down the derivation: scheme, port and path resolution, and -- most
importantly -- which hosts are trusted with an Aion platform token.
"""

import pytest

from aion.server.logging.handlers.logstash.endpoint import (
    is_platform_host,
    resolve_logstash_endpoint,
)

PLATFORM = "api.aion.to"


def _resolve(host, port=None, platform_host=PLATFORM):
    return resolve_logstash_endpoint(host, port, platform_host)


class TestNotConfigured:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_missing_host_resolves_to_none(self, raw):
        assert _resolve(raw) is None


class TestBareHost:
    def test_defaults_to_http_and_port_80(self):
        endpoint = _resolve("logstash.internal")
        assert endpoint.url == "http://logstash.internal:80/"
        assert endpoint.use_platform_auth is False

    def test_uses_logstash_port_when_given(self):
        endpoint = _resolve("localhost", port=5000)
        assert endpoint.url == "http://localhost:5000/"

    def test_port_embedded_in_host_wins_over_setting(self):
        endpoint = _resolve("localhost:5044", port=5000)
        assert endpoint.port == 5044

    def test_bare_platform_host_defaults_to_https(self):
        endpoint = _resolve("logs.aion.to")
        assert endpoint.url == "https://logs.aion.to:443/"
        assert endpoint.use_platform_auth is True


class TestUrlHost:
    def test_full_url_with_path(self):
        endpoint = _resolve("https://logs.aion.to/ingest")
        assert endpoint.scheme == "https"
        assert endpoint.host == "logs.aion.to"
        assert endpoint.port == 443
        assert endpoint.path == "ingest"
        assert endpoint.url == "https://logs.aion.to:443/ingest"

    def test_explicit_port_in_url(self):
        endpoint = _resolve("https://logs.aion.to:8443/ingest")
        assert endpoint.port == 8443

    def test_http_url_defaults_to_port_80(self):
        endpoint = _resolve("http://logstash.internal/in")
        assert endpoint.port == 80
        assert endpoint.use_platform_auth is False

    def test_nested_path_is_preserved(self):
        endpoint = _resolve("https://logs.aion.to/a/b/c")
        assert endpoint.path == "a/b/c"

    @pytest.mark.parametrize("raw", [
        "ftp://logs.aion.to",
        "tcp://logs.aion.to:5044",
    ])
    def test_unsupported_scheme_is_rejected(self, raw):
        with pytest.raises(ValueError, match="unsupported scheme"):
            _resolve(raw)

    def test_missing_hostname_is_rejected(self):
        with pytest.raises(ValueError, match="does not contain a hostname"):
            _resolve("https:///ingest")

    def test_invalid_port_is_rejected(self):
        with pytest.raises(ValueError, match="invalid port"):
            _resolve("https://logs.aion.to:not-a-port/")


class TestPlatformHostMatching:
    """The platform token is a full client credential, so this is the
    security-relevant part: it must never be attached for a foreign host."""

    @pytest.mark.parametrize("host", [
        "aion.to",
        "logs.aion.to",
        "logs.eu.aion.to",
        "LOGS.AION.TO",
        "logs.aion.to.",
    ])
    def test_platform_hosts_are_recognised(self, host):
        assert is_platform_host(host, PLATFORM) is True

    @pytest.mark.parametrize("host", [
        "aion.tools",           # substring match would accept this
        "aion.torture.com",     # and this
        "aion.today",
        "notaion.to",           # suffix without a label boundary
        "logs.aion.to.evil.com",
        "attacker.com",
        "localhost",
    ])
    def test_foreign_hosts_are_rejected(self, host):
        assert is_platform_host(host, PLATFORM) is False

    @pytest.mark.parametrize("host", ["", None])
    def test_empty_inputs_are_rejected(self, host):
        assert is_platform_host(host, PLATFORM) is False
        assert is_platform_host("logs.aion.to", host) is False

    def test_token_is_not_attached_for_foreign_host(self):
        endpoint = _resolve("https://aion.tools/ingest")
        assert endpoint.scheme == "https"
        assert endpoint.use_platform_auth is False

    def test_token_is_not_sent_over_plaintext(self):
        endpoint = _resolve("http://logs.aion.to/ingest")
        assert endpoint.use_platform_auth is False

    def test_platform_domain_follows_api_host(self):
        """A staging environment authenticates without any code change."""
        endpoint = _resolve("logs.aion.dev", platform_host="api.aion.dev")
        assert endpoint.use_platform_auth is True

        endpoint = _resolve("logs.aion.to", platform_host="api.aion.dev")
        assert endpoint.use_platform_auth is False

    def test_no_platform_host_disables_auth(self):
        endpoint = _resolve("https://logs.aion.to/x", platform_host=None)
        assert endpoint.use_platform_auth is False


class TestDescribe:
    def test_describe_reports_auth_state(self):
        assert "platform auth: enabled" in _resolve("https://logs.aion.to").describe()
        assert "platform auth: disabled" in _resolve("localhost", 5000).describe()
