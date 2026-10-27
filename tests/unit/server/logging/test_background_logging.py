"""Logging without an incoming request must retain the running version."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from aion.core.logging.base import AionLogRecord
from aion.server.logging.filters import ServerAionContextFilter
from aion.server.logging.handlers.logstash import AionLogstashFilter, AionLogstashFormatter


@pytest.fixture
def background_context(monkeypatch):
    from aion.server.settings import app_settings

    monkeypatch.setattr(app_settings, "version_id", "running-version")
    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", lambda: None)
    monkeypatch.setattr("aion.server.opentelemetry.tracing.get_span_info", lambda: None)


def format_background(record):
    ServerAionContextFilter().filter(record)
    assert AionLogstashFilter().filter(record)
    return json.loads(AionLogstashFormatter(client_id="client", host_name="worker").format(record))


@pytest.mark.parametrize("record_class", [AionLogRecord, logging.LogRecord])
@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING, logging.ERROR])
def test_startup_and_library_records_ship_without_a_request(background_context, record_class, level):
    record = record_class("app.startup", level, "app.py", 1, "Server starting", (), None)
    data = format_background(record)
    assert data["message"] == "Server starting"
    assert data["_tags"]["aion.version.id"] == "running-version"
    assert data["_tags"]["aion.distribution.id"] is None
    assert data["trace.id"] is None
    assert data["transaction.id"] is None


def test_background_thread_keeps_server_identity_without_a_request(background_context):
    def work():
        return format_background(logging.LogRecord(
            "app.scheduler", logging.INFO, "jobs.py", 1, "Refresh completed", (), None))

    with ThreadPoolExecutor(max_workers=1) as executor:
        data = executor.submit(work).result()
    assert data["_tags"]["aion.version.id"] == "running-version"


def test_unidentified_local_logs_and_debug_are_still_not_shipped(background_context, monkeypatch):
    from aion.server.settings import app_settings

    debug = AionLogRecord("app", logging.DEBUG, "app.py", 1, "debug", (), None)
    ServerAionContextFilter().filter(debug)
    assert not AionLogstashFilter().filter(debug)
    monkeypatch.setattr(app_settings, "version_id", None)
    local = AionLogRecord("app", logging.ERROR, "app.py", 1, "local failure", (), None)
    ServerAionContextFilter().filter(local)
    assert not AionLogstashFilter().filter(local)


def test_request_context_failure_does_not_erase_server_identity(background_context, monkeypatch):
    def unavailable():
        raise RuntimeError("execution scope unavailable")

    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", unavailable)
    data = format_background(AionLogRecord("app", logging.ERROR, "app.py", 1, "Failure", (), None))
    assert data["_tags"]["aion.version.id"] == "running-version"


def test_root_handlers_ship_startup_and_worker_logs_once(background_context, monkeypatch):
    from aion.server.logging import setup_root_logger
    from aion.server.logging.handlers.logstash import AionLogstashHandler
    from aion.server.settings import app_settings

    captured = []
    monkeypatch.setattr(app_settings, "logstash_host", "localhost")
    monkeypatch.setattr(app_settings, "logstash_port", 5000)
    monkeypatch.setattr(app_settings, "log_level", "INFO")
    monkeypatch.setattr(AionLogstashHandler, "emit", lambda self, record: captured.append(
        json.loads(self.format(record))))
    root = logging.getLogger()
    previous, level = root.handlers[:], root.level
    root.handlers = []
    try:
        setup_root_logger()
        setup_root_logger()  # Repeated initialization must not duplicate delivery.
        root.info("Application startup")
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(logging.getLogger("app.scheduler").warning, "Worker recovering").result()
        logging.getLogger("httpx").info("Suppressed dependency detail")
        assert [entry["message"] for entry in captured] == ["Application startup", "Worker recovering"]
        assert all(entry["_tags"]["aion.version.id"] == "running-version" for entry in captured)
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers, root.level = previous, level


def test_request_version_wins_and_does_not_leak_into_the_next_background_record(background_context, monkeypatch):
    from unittest.mock import MagicMock

    scope = MagicMock()
    scope.inbound.aion.version_id = "request-version"
    scope.inbound.aion.distribution_id = "request-distribution"
    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", lambda: scope)
    request = AionLogRecord("app", logging.INFO, "app.py", 1, "Request", (), None)
    ServerAionContextFilter().filter(request)
    assert request.aion_version_id == "request-version"
    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", lambda: None)
    background = format_background(AionLogRecord("app", logging.INFO, "app.py", 1, "Job", (), None))
    assert background["_tags"]["aion.version.id"] == "running-version"
    assert background["_tags"]["aion.distribution.id"] is None
