"""Logging without an incoming request must retain the deployment identity."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from aion.core.logging.base import AionLogRecord
from aion.server.logging.filters import ServerAionContextFilter
from aion.server.logging.handlers.logstash import AionLogstashFilter, AionLogstashFormatter


RUNNING_DEPLOYMENT_ID = "12345678-1234-5678-1234-567812345678"


@pytest.fixture
def background_context(monkeypatch):
    from aion.server.settings import AppSettings, app_settings

    monkeypatch.setenv("ORGANIZATION_ID", "running-organization")
    monkeypatch.setenv("DEPLOYMENT_ID", RUNNING_DEPLOYMENT_ID)
    settings = AppSettings()
    monkeypatch.setattr(app_settings, "organization_id", settings.organization_id)
    monkeypatch.setattr(app_settings, "deployment_id", settings.deployment_id)
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
    assert data["_tags"]["aion.organization.id"] == "running-organization"
    assert data["_tags"]["aion.deployment.id"] == RUNNING_DEPLOYMENT_ID
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
    assert data["_tags"]["aion.organization.id"] == "running-organization"
    assert data["_tags"]["aion.deployment.id"] == RUNNING_DEPLOYMENT_ID


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
    assert data["_tags"]["aion.organization.id"] == "running-organization"
    assert data["_tags"]["aion.deployment.id"] == RUNNING_DEPLOYMENT_ID


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
        assert all(entry["_tags"]["aion.organization.id"] == "running-organization" for entry in captured)
        assert all(entry["_tags"]["aion.deployment.id"] == RUNNING_DEPLOYMENT_ID for entry in captured)
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers, root.level = previous, level


def test_request_identity_wins_and_does_not_leak_into_the_next_background_record(background_context, monkeypatch):
    scope = SimpleNamespace(
        inbound=SimpleNamespace(
            trace=SimpleNamespace(
                baggage={"aion.organization.id": "request-organization", "aion.deployment.id": "request-deployment"},
                transaction_id=None,
            ),
            transaction_name=None,
            aion=SimpleNamespace(version_id="request-version", distribution_id="request-distribution", environment_id=None),
            request=SimpleNamespace(method="POST", path="/", jrpc_method=None),
            a2a=SimpleNamespace(task_id=None, task_status=None),
        ),
        framework=SimpleNamespace(agent_framework=SimpleNamespace(trace=SimpleNamespace(baggage={}))),
    )
    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", lambda: scope)
    request = AionLogRecord("app", logging.INFO, "app.py", 1, "Request", (), None)
    data = format_background(request)
    assert data["_tags"]["aion.version.id"] == "request-version"
    assert data["_tags"]["aion.organization.id"] == "request-organization"
    assert data["_tags"]["aion.deployment.id"] == "request-deployment"
    monkeypatch.setattr("aion.server.agent.execution.scope.get_execution_scope", lambda: None)
    background = format_background(AionLogRecord("app", logging.INFO, "app.py", 1, "Job", (), None))
    assert background["_tags"]["aion.version.id"] == "running-version"
    assert background["_tags"]["aion.organization.id"] == "running-organization"
    assert background["_tags"]["aion.deployment.id"] == RUNNING_DEPLOYMENT_ID
    assert background["_tags"]["aion.distribution.id"] is None


@pytest.mark.parametrize(
    "tag,default,other_tag,other_default",
    [
        ("aion.organization.id", "running-organization", "aion.deployment.id", RUNNING_DEPLOYMENT_ID),
        ("aion.deployment.id", RUNNING_DEPLOYMENT_ID, "aion.organization.id", "running-organization"),
    ],
)
@pytest.mark.parametrize(
    "inbound,framework,expected",
    [
        (None, None, None),
        (None, "", None),
        ("inbound-id", None, "inbound-id"),
        ("inbound-id", "", "inbound-id"),
        (None, "framework-id", "framework-id"),
        ("inbound-id", "framework-id", "framework-id"),
    ],
)
def test_identity_precedence_does_not_mutate_or_reuse_baggage(
    background_context, tag, default, other_tag, other_default, inbound, framework, expected,
):
    record = AionLogRecord("app", logging.INFO, "app.py", 1, "Request", (), None)
    record.trace_baggage = {"inbound.tag": "inbound", tag: inbound}
    record.agent_trace_baggage = {"framework.tag": "framework", tag: framework}
    original_inbound, original_framework = record.trace_baggage.copy(), record.agent_trace_baggage.copy()
    formatter = AionLogstashFormatter(client_id="client", host_name="worker")
    data = json.loads(formatter.format(record))
    assert data["_tags"][tag] == (expected if expected is not None else default)
    assert data["_tags"][other_tag] == other_default
    assert data["_tags"]["inbound.tag"] == "inbound"
    assert data["_tags"]["framework.tag"] == "framework"
    assert record.trace_baggage == original_inbound
    assert record.agent_trace_baggage == original_framework

    background = AionLogRecord("app", logging.INFO, "app.py", 1, "Job", (), None)
    background_data = json.loads(formatter.format(background))
    assert background_data["_tags"][tag] == default
    assert background_data["_tags"][other_tag] == other_default
    assert "inbound.tag" not in background_data["_tags"]
    assert "framework.tag" not in background_data["_tags"]


@pytest.mark.parametrize(
    "setting,tag,other_tag,other_default",
    [
        ("organization_id", "aion.organization.id", "aion.deployment.id", RUNNING_DEPLOYMENT_ID),
        ("deployment_id", "aion.deployment.id", "aion.organization.id", "running-organization"),
    ],
)
@pytest.mark.parametrize("default", [None, ""])
def test_missing_deployment_setting_leaves_request_attribution_intact(
    background_context, monkeypatch, setting, tag, other_tag, other_default, default,
):
    from aion.server.settings import app_settings

    monkeypatch.setattr(app_settings, setting, default)
    record = AionLogRecord("app", logging.INFO, "app.py", 1, "Local", (), None)
    data = format_background(record)
    assert tag not in data["_tags"]
    assert data["_tags"][other_tag] == other_default
    record.trace_baggage = {tag: "request-id"}
    data = format_background(record)
    assert data["_tags"][tag] == "request-id"
    assert data["_tags"][other_tag] == other_default
