"""Tests for aion.server.utils.logging and aion.server.logging.filters.NamespaceFilter."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from aion.server.logging.filters import BASE_RULES, NamespaceFilter


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


class TestBaseRules:
    """The shipped rules muzzle library chatter LOG_LEVEL alone would let through."""

    def _record(self, name: str, level: int) -> logging.LogRecord:
        return logging.LogRecord(
            name=name, level=level, pathname="x.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )

    def test_base_rules_muzzle_gql_transport_chatter(self):
        """gql logs its websocket frames at INFO, which is noise in a server log."""
        f = NamespaceFilter(BASE_RULES)

        assert not f.filter(self._record("gql.transport.websockets", logging.INFO))

    def test_base_rules_muzzle_websockets_frame_tracing(self):
        """websockets traces every frame at DEBUG, keepalive ping/pong included.

        Left alone under LOG_LEVEL=DEBUG that is roughly fourteen lines a minute
        per agent, and the handshake dump among them spells out the auth token
        in the request line.
        """
        f = NamespaceFilter(BASE_RULES)

        assert not f.filter(self._record("websockets.client", logging.DEBUG))
        assert not f.filter(self._record("websockets.protocol", logging.INFO))

    def test_websockets_failures_are_kept(self):
        """The rule trims the frame tracing, not the diagnosis.

        Transfer and keepalive failures are logged at ERROR by the library, and
        those are the ones that explain an outage.
        """
        f = NamespaceFilter(BASE_RULES)

        assert f.filter(self._record("websockets.client", logging.ERROR))

    def test_base_rules_muzzle_streamed_chunk_dumps(self):
        """sse_starlette logs each streamed chunk whole, payload included.

        A single agent reply is hundreds of them, so at LOG_LEVEL=DEBUG the
        stream is both the bulk of the log and a copy of the task's contents.
        """
        f = NamespaceFilter(BASE_RULES)

        assert not f.filter(self._record("sse_starlette.sse", logging.DEBUG))
        assert f.filter(self._record("sse_starlette.sse", logging.WARNING))


class TestShieldedWebsocketCloseFilter:
    """A handled reconnect must not read as an error nobody dealt with.

    asyncio reports the close a second time, at ERROR with a full traceback,
    because websockets awaits its teardown under a shield and nobody retrieves
    the inner exception. We do retrieve it, and report the same close as one
    warning naming the reason and the connection's age.
    """

    def _filter(self):
        from aion.server.logging.filters import ShieldedWebsocketCloseFilter

        return ShieldedWebsocketCloseFilter()

    def _record(self, msg: str, exc: BaseException | None, name: str = "asyncio"):
        return logging.LogRecord(
            name=name, level=logging.ERROR, pathname="x.py", lineno=1,
            msg=msg, args=(),
            exc_info=(type(exc), exc, exc.__traceback__) if exc else None,
        )

    @staticmethod
    def _closed_error():
        from websockets.exceptions import ConnectionClosedError

        return ConnectionClosedError(None, None)

    def test_the_duplicate_close_report_is_dropped(self):
        record = self._record(
            "ConnectionClosedError exception in shielded future\nfuture: <Future ...>",
            self._closed_error())

        assert not self._filter().filter(record)

    def test_an_unretrieved_task_exception_still_gets_through(self):
        """The class of bug that once hid a dropped background task from us."""
        record = self._record(
            "Task exception was never retrieved\nfuture: <Task ...>", self._closed_error())

        assert self._filter().filter(record)

    def test_a_shielded_future_from_elsewhere_still_gets_through(self):
        """The rule is about one library's teardown, not about shields at large."""
        record = self._record(
            "ValueError exception in shielded future", ValueError("something ours"))

        assert self._filter().filter(record)

    def test_the_same_words_from_another_logger_get_through(self):
        record = self._record(
            "ConnectionClosedError exception in shielded future",
            self._closed_error(), name="aion.server.core.platform")

        assert self._filter().filter(record)

    def test_a_record_without_an_exception_gets_through(self):
        """asyncio calls its handler with no exception attached too."""
        record = self._record("exception in shielded future", None)

        assert self._filter().filter(record)

    def test_both_handlers_carry_the_filter(self):
        """Logstash is where the false alert would land, so it needs it too."""
        from aion.server.logging.filters import ShieldedWebsocketCloseFilter
        from aion.server.logging.handlers import AionLogstashHandler, LogStreamHandler
        from aion.server.logging import setup_root_logger

        root = logging.getLogger()
        # Both are cleared, and restored afterwards: setup_root_logger bails out
        # if a stream handler is already attached, and a logstash handler left
        # behind by an earlier test would be counted as one of ours.
        original = root.handlers[:]
        root.handlers = [
            h for h in original
            if not isinstance(h, (LogStreamHandler, AionLogstashHandler))
        ]

        try:
            with patch("aion.server.settings.app_settings") as ms, \
                 patch("aion.core.settings.api_settings") as ma:
                ms.log_level = logging.DEBUG
                ms.logstash_host = "localhost"
                ms.logstash_port = 5000
                ms.is_logstash_configured = False
                ms.node_name = "n"
                ma.client_id = "c"

                setup_root_logger()

            installed = [
                h for h in root.handlers
                if any(isinstance(f, ShieldedWebsocketCloseFilter) for f in h.filters)
            ]
            assert len(installed) == 2, "both the stream and the logstash handler need it"
        finally:
            root.handlers = original


class TestPushNotificationLogging:
    """The blanket a2a rule must not reach the sender that carries the diagnosis.

    There is deliberately no rule for the SDK's own push-notification module:
    AuthenticatedPushNotificationSender overrides both of its logging methods
    and never delegates, so that module emits nothing here and a rule naming it
    would only pin us to an SDK-internal path that an upgrade can rename
    silently. What has to hold is the pair below.
    """

    def _record(self, name: str, level: int) -> logging.LogRecord:
        return logging.LogRecord(
            name=name, level=level, pathname="x.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )

    def test_our_own_sender_still_reports_failures(self):
        """Our sender logs under aion.*, which no rule narrows."""
        f = NamespaceFilter(BASE_RULES)

        assert f.filter(self._record(
            "aion.server.tasks.authenticated_push_sender", logging.WARNING))

    def test_a2a_warnings_are_kept(self):
        """The a2a rule trims the namespace to WARNING, it does not silence it."""
        f = NamespaceFilter(BASE_RULES)

        assert f.filter(self._record("a2a.server.tasks.task_manager", logging.WARNING))
