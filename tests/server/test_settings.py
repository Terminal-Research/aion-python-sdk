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
