"""Logging filter that injects OpenTelemetry and execution-scope context into log records."""

import logging

# Rules for stream handler: namespace -> minimum log level, None = exclude entirely
BASE_RULES: dict[str, int | None] = {
    "httpcore": logging.WARNING,
    "httpx": logging.WARNING,
    "asyncio": logging.WARNING,
    "urllib3": logging.WARNING,
    "multipart": logging.WARNING,
    "charset_normalizer": logging.WARNING,
    "uvicorn": logging.WARNING,
    "uvicorn.access": logging.WARNING,
    "a2a": logging.WARNING,
    "alembic": logging.WARNING,
}


class NamespaceFilter(logging.Filter):
    """Filter log records by logger namespace with per-namespace minimum log levels.

    Rules are matched by longest prefix first, so more specific namespaces
    override parent rules (e.g. "uvicorn.access" overrides "uvicorn").
    A level of None means the namespace is excluded entirely.
    """

    def __init__(self, rules: dict[str, int | None]):
        super().__init__()
        self._rules = sorted(rules.items(), key=lambda x: len(x[0]), reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        for namespace, level in self._rules:
            if record.name == namespace or record.name.startswith(namespace + "."):
                if level is None:
                    return False
                return record.levelno >= level
        return True


class ServerAionContextFilter(logging.Filter):
    """
    Enriches logging.LogRecord with OpenTelemetry tracing and server execution context.

    Attach to a logger in _configure_logger so all handlers receive enriched records
    before any filtering or formatting takes place.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        self._enrich_otel(record)
        self._enrich_server_context(record)
        return True

    @staticmethod
    def _enrich_otel(record: logging.LogRecord) -> None:
        """Populate trace_id, span_id, span_name, and parent_span_id from the active OTel span."""
        try:
            from aion.server.opentelemetry.tracing import get_span_info

            trace_span_info = get_span_info()
            record.trace_id = getattr(trace_span_info, "trace_id_hex", None)
            record.trace_span_id = getattr(trace_span_info, "span_id_hex", None)
            record.trace_span_name = getattr(trace_span_info, "span_name", None)
            record.trace_parent_span_id = getattr(trace_span_info, "parent_span_id_hex", None)
        except Exception:
            pass

    @classmethod
    def _enrich_server_context(cls, record: logging.LogRecord) -> None:
        """Populate Aion deployment, task, and request fields from the current execution scope."""
        try:
            from aion.server.agent.execution.scope import get_execution_scope

            scope = get_execution_scope()
            if not scope:
                return

            ec_inbound = scope.inbound
            ec_framework = scope.framework

            record.trace_baggage = ec_inbound.trace.baggage.copy()
            record.agent_trace_baggage = ec_framework.agent_framework.trace.baggage.copy()

            record.transaction_id = ec_inbound.trace.transaction_id
            record.transaction_name = ec_inbound.transaction_name

            record.aion_distribution_id = ec_inbound.aion.distribution_id
            record.aion_version_id = ec_inbound.aion.version_id or cls._get_app_version_id()
            record.aion_agent_environment_id = ec_inbound.aion.environment_id

            record.http_request_method = ec_inbound.request.method
            record.http_request_target = ec_inbound.request.path

            record.task_id = ec_inbound.a2a.task_id
            record.a2a_rpc_method = ec_inbound.request.jrpc_method
            record.a2a_task_status = ec_inbound.a2a.task_status
        except Exception:
            pass

    @staticmethod
    def _get_app_version_id() -> str:
        """Return the running agent's version_id from app settings as a fallback."""
        from aion.server.settings import app_settings
        return app_settings.version_id
