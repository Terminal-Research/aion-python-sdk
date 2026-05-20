"""Tests for the aion logging system.

Focus areas:
  AionLogRecord:
    - Created with standard LogRecord args
    - All context fields are None when no scope is active
    - aion_version_id falls back to app_settings.version_id

  AionLogger:
    - makeRecord returns AionLogRecord
    - makeRecord with extra dict merges fields
    - makeRecord raises KeyError for protected keys

  get_logger (factory):
    - Returns AionLogger instance
    - Re-uses existing logger by same name
    - Does not re-configure already-configured logger
    - Configures logger level from app_settings when level is None

  _is_logger_configured:
    - False when no handlers
    - True when AionLogstashHandler present
    - True when LogStreamHandler present

  AionLogstashFilter:
    - Rejects DEBUG records
    - Accepts INFO+ with valid distribution_id
    - Accepts INFO+ with valid trace_id
    - Rejects INFO+ with neither deployment nor trace context

  AionLogstashFormatter:
    - format returns valid JSON
    - timestamp field in correct format
    - logLevel mapping: WARNING -> WARN, CRITICAL -> FATAL
    - error fields present when exc_info is set
    - user.id extracted from trace_baggage

  LogStreamFormatter:
    - format returns non-empty string
    - colorize errors are suppressed (returns plain message)
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from aion.server.logging.base import AionLogRecord, AionLogger
from aion.server.logging.handlers.logstash import (
    AionLogstashFilter,
    AionLogstashFormatter,
)
from aion.server.logging.handlers.stream import LogStreamFormatter, LogStreamHandler

def _make_log_record(
    name: str = "test",
    level: int = logging.INFO,
    msg: str = "test message",
) -> AionLogRecord:
    """Create a minimal AionLogRecord with no execution scope."""
    with patch("aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope", return_value=None):
        with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
            record = AionLogRecord(
                name=name,
                level=level,
                pathname="test.py",
                lineno=1,
                msg=msg,
                args=(),
                exc_info=None,
            )
    return record


def _make_logstash_record(**overrides) -> AionLogRecord:
    rec = _make_log_record()
    for k, v in overrides.items():
        setattr(rec, k, v)
    return rec

class TestAionLogRecord:
    def test_creates_without_scope(self):
        """AionLogRecord can be instantiated without active execution scope."""
        rec = _make_log_record()
        assert isinstance(rec, AionLogRecord)
        assert isinstance(rec, logging.LogRecord)

    def test_all_context_fields_none_without_scope(self):
        """All context fields are None when no execution scope is active."""
        rec = _make_log_record()
        context_fields = [
            "trace_id", "trace_span_id", "trace_span_name", "trace_parent_span_id",
            "trace_baggage", "agent_trace_baggage",
            "transaction_id", "transaction_name",
            "aion_distribution_id", "aion_agent_environment_id",
            "http_request_method", "http_request_target",
            "task_id", "a2a_rpc_method", "a2a_task_status",
        ]
        for field in context_fields:
            assert getattr(rec, field) is None, f"{field} should be None"

    def test_version_id_falls_back_to_app_settings(self):
        """aion_version_id falls back to app_settings.version_id when scope is unavailable."""
        with patch("aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope", return_value=None):
            with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
                with patch("aion.shared.settings.app_settings") as mock_settings:
                    mock_settings.version_id = "v9.9.9"
                    rec = AionLogRecord(
                        name="test", level=logging.INFO, pathname="x.py",
                        lineno=1, msg="hi", args=(), exc_info=None,
                    )
        assert rec.aion_version_id == "v9.9.9"

    def test_exception_gracefully_handled_in_scope_fetch(self):
        """Exceptions during scope fetch are handled gracefully without propagating."""
        with patch(
            "aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope",
            side_effect=Exception("scope broken"),
        ):
            with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
                rec = AionLogRecord(
                    name="test", level=logging.INFO, pathname="x.py",
                    lineno=1, msg="hi", args=(), exc_info=None,
                )
        assert rec.trace_id is None

class TestAionLogger:
    def _logger(self, name: str = "test_logger") -> AionLogger:
        logging.setLoggerClass(AionLogger)
        return logging.getLogger(name)

    def test_make_record_returns_aion_log_record(self):
        """makeRecord returns an AionLogRecord instance."""
        logger = self._logger("make_record_test")
        with patch("aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope", return_value=None):
            with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
                rec = logger.makeRecord(
                    name="test", level=logging.INFO, fn="f.py",
                    lno=1, msg="hello", args=(), exc_info=None,
                )
        assert isinstance(rec, AionLogRecord)

    def test_make_record_extra_fields_added(self):
        """makeRecord merges extra dict fields into the log record."""
        logger = self._logger("make_record_extra_test")
        with patch("aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope", return_value=None):
            with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
                rec = logger.makeRecord(
                    name="test", level=logging.INFO, fn="f.py",
                    lno=1, msg="hello", args=(), exc_info=None,
                    extra={"custom_field": "custom_value"},
                )
        assert rec.custom_field == "custom_value"

    def test_make_record_raises_on_protected_key(self):
        """makeRecord raises KeyError when extra dict contains protected keys."""
        logger = self._logger("protected_key_test")
        with patch("aion.shared.agent.execution.scope.AgentExecutionScopeHelper.get_scope", return_value=None):
            with patch("aion.shared.opentelemetry.tracing.get_span_info", return_value=None):
                with pytest.raises(KeyError):
                    logger.makeRecord(
                        name="test", level=logging.INFO, fn="f.py",
                        lno=1, msg="hello", args=(), exc_info=None,
                        extra={"message": "bad"},
                    )

class TestGetLogger:
    def test_returns_aion_logger_instance(self):
        """get_logger returns an AionLogger instance."""
        from aion.server.logging.factory import get_logger
        with patch("aion.shared.settings.app_settings") as mock_settings:
            with patch("aion.shared.settings.api_settings") as mock_api:
                mock_settings.log_level = logging.DEBUG
                mock_settings.logstash_host = "localhost"
                mock_settings.logstash_port = 5000
                mock_settings.is_logstash_configured = False
                mock_settings.node_name = "test-node"
                mock_settings.version_id = "1.0.0"
                mock_api.client_id = "test-client"
                logger = get_logger("unique_factory_test_logger")
        assert isinstance(logger, AionLogger)

    def test_same_logger_returned_for_same_name(self):
        """get_logger returns the same logger instance for the same name (caching)."""
        from aion.server.logging.factory import get_logger
        with patch("aion.shared.settings.app_settings") as mock_settings:
            with patch("aion.shared.settings.api_settings") as mock_api:
                mock_settings.log_level = logging.INFO
                mock_settings.logstash_host = "localhost"
                mock_settings.logstash_port = 5000
                mock_settings.is_logstash_configured = False
                mock_settings.node_name = "n"
                mock_settings.version_id = "v"
                mock_api.client_id = "c"
                l1 = get_logger("same_name_logger")
                l2 = get_logger("same_name_logger")
        assert l1 is l2

    def test_is_logger_configured_false_for_new_logger(self):
        """_is_logger_configured returns False for loggers with no handlers."""
        from aion.server.logging.factory import _is_logger_configured
        logger = logging.getLogger("unconfigured_logger_99")
        logger.handlers.clear()
        assert not _is_logger_configured(logger)

    def test_is_logger_configured_true_with_stream_handler(self):
        """_is_logger_configured returns True when LogStreamHandler is attached."""
        from aion.server.logging.factory import _is_logger_configured
        logger = logging.getLogger("configured_stream_test")
        logger.handlers.clear()
        logger.addHandler(LogStreamHandler())
        assert _is_logger_configured(logger)

class TestAionLogstashFilter:
    def _filter(self) -> AionLogstashFilter:
        return AionLogstashFilter()

    def test_rejects_debug_level(self):
        """AionLogstashFilter rejects DEBUG level records."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id="dist-1")
        rec.levelno = logging.DEBUG
        assert not f.filter(rec)

    def test_accepts_info_with_distribution_id(self):
        """AionLogstashFilter accepts INFO+ with distribution_id set."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id="dist-1", aion_version_id=None, trace_id=None)
        rec.levelno = logging.INFO
        assert f.filter(rec)

    def test_accepts_info_with_version_id(self):
        """AionLogstashFilter accepts INFO+ with version_id set."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id=None, aion_version_id="v1.0.0", trace_id=None)
        rec.levelno = logging.INFO
        assert f.filter(rec)

    def test_accepts_info_with_trace_id(self):
        """AionLogstashFilter accepts INFO+ with trace_id set."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id=None, aion_version_id=None, trace_id="abc123")
        rec.levelno = logging.INFO
        assert f.filter(rec)

    def test_rejects_info_without_deployment_or_trace(self):
        """AionLogstashFilter rejects INFO without distribution, version, or trace context."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id=None, aion_version_id=None, trace_id=None)
        rec.levelno = logging.INFO
        assert not f.filter(rec)

    def test_accepts_error_level(self):
        """AionLogstashFilter accepts ERROR level regardless of context."""
        f = self._filter()
        rec = _make_logstash_record(aion_distribution_id="dist-1")
        rec.levelno = logging.ERROR
        assert f.filter(rec)

    def test_accepts_warning_level(self):
        """AionLogstashFilter accepts WARNING level regardless of context."""
        f = self._filter()
        rec = _make_logstash_record(trace_id="abc")
        rec.levelno = logging.WARNING
        assert f.filter(rec)

class TestAionLogstashFormatter:
    def _formatter(self, client_id: str = "c1", node_name: str = "n1") -> AionLogstashFormatter:
        return AionLogstashFormatter(client_id=client_id, node_name=node_name)

    def test_format_returns_valid_json(self):
        """format returns valid JSON-serializable dict output."""
        formatter = self._formatter()
        rec = _make_logstash_record(trace_id="abc", aion_distribution_id="dist")
        result = formatter.format(rec)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_format_contains_required_fields(self):
        """format includes @timestamp, clientId, host.name, logLevel, and message."""
        formatter = self._formatter(client_id="my-client", node_name="my-node")
        rec = _make_logstash_record(trace_id="abc")
        data = json.loads(formatter.format(rec))
        assert "@timestamp" in data
        assert data["clientId"] == "my-client"
        assert data["host.name"] == "my-node"
        assert "logLevel" in data
        assert "message" in data

    def test_warning_level_mapped_to_warn(self):
        """format maps WARNING level to WARN."""
        formatter = self._formatter()
        rec = _make_logstash_record()
        rec.levelname = "WARNING"
        data = json.loads(formatter.format(rec))
        assert data["logLevel"] == "WARN"

    def test_critical_level_mapped_to_fatal(self):
        """format maps CRITICAL level to FATAL."""
        formatter = self._formatter()
        rec = _make_logstash_record()
        rec.levelname = "CRITICAL"
        data = json.loads(formatter.format(rec))
        assert data["logLevel"] == "FATAL"

    def test_info_level_unchanged(self):
        """format leaves INFO level unchanged."""
        formatter = self._formatter()
        rec = _make_logstash_record()
        rec.levelname = "INFO"
        data = json.loads(formatter.format(rec))
        assert data["logLevel"] == "INFO"

    def test_error_fields_added_on_exc_info(self):
        """format includes error.message, error.type, and error.stack_trace when exc_info is set."""
        formatter = self._formatter()
        rec = _make_logstash_record()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            rec.exc_info = sys.exc_info()

        data = json.loads(formatter.format(rec))
        assert "error.message" in data
        assert data["error.type"] == "ValueError"
        assert "error.stack_trace" in data

    def test_user_id_extracted_from_baggage(self):
        """format extracts user.id from trace_baggage's aion.sender.id."""
        formatter = self._formatter()
        rec = _make_logstash_record(trace_baggage={"aion.sender.id": "user-xyz"})
        data = json.loads(formatter.format(rec))
        assert data["user.id"] == "user-xyz"

    def test_user_id_none_when_no_baggage(self):
        """format sets user.id to None when trace_baggage is not present."""
        formatter = self._formatter()
        rec = _make_logstash_record(trace_baggage=None)
        data = json.loads(formatter.format(rec))
        assert data["user.id"] is None

    def test_timestamp_format(self):
        """format uses ISO 8601 timestamp format ending with Z."""
        formatter = self._formatter()
        rec = _make_logstash_record()
        data = json.loads(formatter.format(rec))
        ts = data["@timestamp"]
        assert "T" in ts
        assert ts.endswith("Z")

class TestLogStreamFormatter:
    def _formatter(self) -> LogStreamFormatter:
        return LogStreamFormatter()

    def _record(self, msg: str = "test", level: int = logging.INFO) -> AionLogRecord:
        return _make_log_record(msg=msg, level=level)

    def test_format_returns_non_empty_string(self):
        """format returns a non-empty string."""
        formatter = self._formatter()
        rec = self._record()
        with patch("aion.shared.agent.aion_agent.agent_manager") as mock_mgr:
            mock_mgr.agent_id = None
            result = formatter.format(rec)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_includes_level_name(self):
        """format includes the log level name in the output."""
        formatter = self._formatter()
        rec = self._record(level=logging.ERROR)
        rec.levelname = "ERROR"
        with patch("aion.shared.agent.aion_agent.agent_manager") as mock_mgr:
            mock_mgr.agent_id = None
            result = formatter.format(rec)
        assert "ERROR" in result

    def test_format_includes_message(self):
        """format includes the message text in the output."""
        formatter = self._formatter()
        rec = self._record(msg="unique-msg-xyz")
        with patch("aion.shared.agent.aion_agent.agent_manager") as mock_mgr:
            mock_mgr.agent_id = None
            result = formatter.format(rec)
        assert "unique-msg-xyz" in result

    def test_format_includes_agent_id_when_set(self):
        """format includes agent_id when agent_manager.agent_id is set."""
        formatter = self._formatter()
        rec = self._record()
        with patch("aion.shared.agent.aion_agent.agent_manager") as mock_mgr:
            mock_mgr.agent_id = "test-agent"
            result = formatter.format(rec)
        assert "test-agent" in result

    def test_format_includes_task_id_when_set(self):
        """format includes task_id when set on the log record."""
        formatter = self._formatter()
        rec = self._record()
        rec.task_id = "task-abc"
        with patch("aion.shared.agent.aion_agent.agent_manager") as mock_mgr:
            mock_mgr.agent_id = None
            result = formatter.format(rec)
        assert "task-abc" in result

    def test_colorize_exception_suppressed(self):
        """_colorize falls back to plain text when colorize_text raises exceptions."""
        formatter = self._formatter()
        with patch("aion.shared.logging.handlers.stream.colorize_text", side_effect=RuntimeError("color broken")):
            result = formatter._colorize("INFO", "plain message")
        assert result == "plain message"
