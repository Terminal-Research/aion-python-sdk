"""Tests for aion.server.settings.AppSettings."""

import pytest

from aion.server.settings import AppSettings


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


def test_encryption_key_without_cryptography_names_the_server_extras(
    run_python_without,
) -> None:
    """The key is unusable without a library only a server extra installs.

    A subprocess, because cryptography is imported by the time this module is
    collected and the message only exists on the path where it is not.
    """
    result = run_python_without(
        ("cryptography",),
        """
        from aion.server.settings import AppSettings

        try:
            AppSettings(ENCRYPTION_KEY="not-a-key")
        except Exception as exc:
            print(exc)
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "ENCRYPTION_KEY is set but" in result.stdout
    assert 'pip install "aionto-sdk[langgraph-server]"' in result.stdout
    assert 'pip install "aionto-sdk[adk-server]"' in result.stdout
    assert "[server]" not in result.stdout
