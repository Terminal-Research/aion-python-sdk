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
    "gql": logging.WARNING,
    # Logs every streamed chunk whole at DEBUG. One agent reply is hundreds of
    # them, each carrying the full task payload - the user's own text, the
    # distribution metadata - into stdout and on to logstash.
    "sse_starlette": logging.WARNING,
    # Traces every frame at DEBUG: ~14 lines a minute of keepalive ping/pong alone,
    # plus a handshake dump that spells out the auth token in the request line.
    # Failures stay visible: it reports transfer and keepalive errors at ERROR.
    "websockets": logging.WARNING,
    "a2a": logging.WARNING,
    "alembic": logging.WARNING,
}


class NamespaceFilter(logging.Filter):
    """Filter log records by logger namespace with per-namespace minimum log levels.

    Rules are matched by longest prefix first, so more specific namespaces
    override parent rules (e.g. "uvicorn.access" overrides "uvicorn").
    A level of None means the namespace is excluded entirely.

    A namespace no rule names is left to LOG_LEVEL, which the root logger
    already enforces.
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


class ShieldedWebsocketCloseFilter(logging.Filter):
    """Drop asyncio's duplicate account of a websocket close we already handled.

    ``websockets`` awaits its own teardown under ``asyncio.shield``. When the
    outer future is abandoned, CPython attaches ``_log_on_exception`` to the
    inner one (asyncio/tasks.py), which hands the close exception to the loop's
    exception handler - and that reports it at ERROR with a full traceback.

    The record is a duplicate by construction: it fires only for an exception
    nobody retrieved from the shield, and this one we do retrieve, through
    ``wait_closed()`` and ``close_exception``, then report as a warning naming
    both the reason and how long the connection lasted. So every reconnect cost
    a twenty-line ERROR describing an event we handled and recovered from within
    a second, which downstream is an alert that means nothing.

    Deliberately narrow: asyncio's own logger, this one message, and only when
    the exception came from websockets. Everything else asyncio reports still
    gets through - "Task exception was never retrieved" above all, which is how
    a dropped background task announces itself.
    """

    _MESSAGE = "exception in shielded future"

    def filter(self, record: logging.LogRecord) -> bool:
        # Ordered so that all but a handful of records leave on one comparison.
        if record.name != "asyncio" or self._MESSAGE not in record.getMessage():
            return True

        error = record.exc_info[1] if record.exc_info else None
        return not (error and type(error).__module__.startswith("websockets"))


class ServerAionContextFilter(logging.Filter):
    """
    Enriches logging.LogRecord with OpenTelemetry tracing and server execution context.

    Attach to a handler so all records receive enriched fields before filtering or formatting.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, '_aion_enriched', False):
            self._enrich_otel(record)
            self._enrich_server_context(record)
            record._aion_enriched = True
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
