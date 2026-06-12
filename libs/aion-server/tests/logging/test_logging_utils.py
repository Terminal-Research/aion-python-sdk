"""Tests for aion.server.utils.logging and aion.server.logging.filters.NamespaceFilter."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from aion.server.logging.filters import BASE_RULES, LOGSTASH_OVERRIDES, NamespaceFilter


class TestNamespaceFilter:
    def _filter(self, rules=None):
        return NamespaceFilter(rules if rules is not None else BASE_RULES)

    def _record(self, name: str, level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name=name, level=level, pathname="x.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )

    def test_allows_unknown_namespace(self):
        """Records from namespaces not in rules pass through."""
        f = self._filter()
        assert f.filter(self._record("myapp.service"))

    def test_excludes_none_rule(self):
        """Namespaces with None level are excluded entirely."""
        f = self._filter({"logstash_async": None})
        assert not f.filter(self._record("logstash_async.transport"))
        assert not f.filter(self._record("logstash_async"))

    def test_level_filter_blocks_below_threshold(self):
        """Records below the namespace threshold are blocked."""
        f = self._filter({"httpx": logging.WARNING})
        assert not f.filter(self._record("httpx", logging.INFO))
        assert not f.filter(self._record("httpx.core", logging.DEBUG))

    def test_level_filter_allows_at_threshold(self):
        """Records at or above the namespace threshold pass."""
        f = self._filter({"httpx": logging.WARNING})
        assert f.filter(self._record("httpx", logging.WARNING))
        assert f.filter(self._record("httpx", logging.ERROR))

    def test_longer_prefix_wins(self):
        """More specific namespace rule overrides parent rule."""
        rules = {"uvicorn": logging.WARNING, "uvicorn.access": logging.INFO}
        f = self._filter(rules)
        assert f.filter(self._record("uvicorn.access", logging.INFO))
        assert not f.filter(self._record("uvicorn.error", logging.INFO))

    def test_exact_name_match(self):
        """Exact namespace name matches (not only prefix)."""
        f = self._filter({"logstash_async": None})
        assert not f.filter(self._record("logstash_async"))

    def test_logstash_overrides_stricter_than_base(self):
        """LOGSTASH_OVERRIDES raises minimum level compared to BASE_RULES."""
        logstash_rules = {**BASE_RULES, **LOGSTASH_OVERRIDES}
        base_filter = NamespaceFilter(BASE_RULES)
        logstash_filter = NamespaceFilter(logstash_rules)

        for name, override_level in LOGSTASH_OVERRIDES.items():
            base_level = BASE_RULES.get(name)
            if base_level is not None and override_level is not None:
                assert override_level >= base_level, (
                    f"LOGSTASH_OVERRIDES[{name!r}] should be >= BASE_RULES[{name!r}]"
                )


class TestSetupRootLogger:
    def test_idempotent_on_repeated_calls(self):
        """setup_root_logger does not add duplicate handlers on repeated calls."""
        from aion.server.logging.handlers import LogStreamHandler
        from aion.server.logging import setup_root_logger

        root = logging.getLogger()
        root.handlers = [h for h in root.handlers if not isinstance(h, LogStreamHandler)]

        with patch("aion.server.settings.app_settings") as ms, \
             patch("aion.core.settings.api_settings") as ma:
            ms.log_level = logging.DEBUG
            ms.logstash_host = "localhost"
            ms.logstash_port = 5000
            ms.is_logstash_configured = False
            ms.node_name = "n"
            ma.client_id = "c"

            setup_root_logger()
            count_after_first = sum(1 for h in root.handlers if isinstance(h, LogStreamHandler))
            setup_root_logger()
            count_after_second = sum(1 for h in root.handlers if isinstance(h, LogStreamHandler))

        assert count_after_first == count_after_second == 1
