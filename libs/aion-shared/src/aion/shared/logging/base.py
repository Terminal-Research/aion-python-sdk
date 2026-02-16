from __future__ import annotations

import logging
from typing import Optional


class AionLogRecord(logging.LogRecord):
    """
    Custom LogRecord that captures a snapshot of the execution context at log time.

    Extends logging.LogRecord with fields from ExecutionContext (inbound + runtime),
    OpenTelemetry span info, and Aion deployment metadata.

    Attributes:
        # OpenTelemetry tracing
        trace_id (Optional[str]): Active trace ID in hex format.
        trace_span_id (Optional[str]): Current span ID in hex format.
        trace_span_name (Optional[str]): Current span name.
        trace_parent_span_id (Optional[str]): Parent span ID in hex format.
        trace_baggage (Optional[dict]): Snapshot of W3C baggage from inbound trace context.
        agent_trace_baggage (Optional[dict]): Snapshot of agent framework baggage from runtime context.

        # Request / transaction
        transaction_id (Optional[str]): Unique identifier of the current transaction.
        transaction_name (Optional[str]): Human-readable transaction name (method + path + rpc method).

        # Aion deployment
        aion_distribution_id (Optional[str]): Agent distribution identifier.
        aion_version_id (Optional[str]): Agent version identifier (falls back to app_settings.version_id).
        aion_agent_environment_id (Optional[str]): Agent environment identifier.

        # HTTP request
        http_request_method (Optional[str]): HTTP method of the incoming request (e.g. POST).
        http_request_target (Optional[str]): HTTP request path / target URI.

        # A2A protocol
        task_id (Optional[str]): A2A task identifier.
        a2a_rpc_method (Optional[str]): JSON-RPC method name of the A2A call.
        a2a_task_status (Optional[str]): Current state of the A2A task.
    """
    trace_id: Optional[str]
    trace_span_id: Optional[str]
    trace_span_name: Optional[str]
    trace_parent_span_id: Optional[str]
    trace_baggage: Optional[dict]
    agent_trace_baggage: Optional[dict]
    transaction_id: Optional[str]
    transaction_name: Optional[str]
    aion_distribution_id: Optional[str]
    aion_version_id: Optional[str]
    aion_agent_environment_id: Optional[str]
    http_request_method: Optional[str]
    http_request_target: Optional[str]
    task_id: Optional[str]
    a2a_rpc_method: Optional[str]
    a2a_task_status: Optional[str]

    def __init__(self, *args, **kwargs):
        """
        Initialize AionLogRecord with request context.

        Args:
            *args: Positional arguments passed to logging.LogRecord
            **kwargs: Keyword arguments passed to logging.LogRecord
        """
        from aion.shared.context import get_context
        from aion.shared.opentelemetry.tracing import get_span_info
        from aion.shared.settings import app_settings

        super().__init__(*args, **kwargs)

        try:
            execution_context = get_context()
        except Exception:
            execution_context = None

        try:
            trace_span_info = get_span_info()
        except Exception:
            trace_span_info = None

        ec_inbound = execution_context.inbound if execution_context else None
        ec_runtime = execution_context.runtime if execution_context else None

        # Opentelemetry tracing
        self.trace_id = getattr(trace_span_info, "trace_id_hex", None)
        self.trace_span_id = getattr(trace_span_info, "span_id_hex", None)
        self.trace_span_name = getattr(trace_span_info, "span_name", None)
        self.trace_parent_span_id = getattr(trace_span_info, "parent_span_id_hex", None)
        self.trace_baggage = ec_inbound.trace.baggage.copy() if execution_context else None
        self.agent_trace_baggage = ec_runtime.agent_framework.trace.baggage.copy() if execution_context else None

        # request context / deployment info
        self.transaction_id = ec_inbound.trace.transaction_id if execution_context else None
        self.transaction_name = ec_inbound.transaction_name if execution_context else None

        self.aion_distribution_id = ec_inbound.aion.distribution_id if execution_context else None
        self.aion_version_id = ec_inbound.aion.version_id if execution_context else app_settings.version_id
        self.aion_agent_environment_id = ec_inbound.aion.environment_id if execution_context else None
        self.http_request_method = ec_inbound.request.method if execution_context else None
        self.http_request_target = ec_inbound.request.path if execution_context else None
        self.task_id = ec_inbound.a2a.task_id if execution_context else None
        self.a2a_rpc_method = ec_inbound.request.jrpc_method if execution_context else None
        self.a2a_task_status = ec_inbound.a2a.task_status if execution_context else None


class AionLogger(logging.Logger):
    """
    Custom Logger that creates AionLogRecord instances.

    This logger extends the standard logging.Logger to use AionLogRecord
    instead of the default LogRecord, enabling automatic capture of
    request context information in all log entries.
    """

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info,
                   func=None, extra=None, sinfo=None) -> AionLogRecord:
        """
        Create a custom AionLogRecord instance.

        Factory method for creating log records with request context.

        Args:
            name: Logger name
            level: Numeric logging level (DEBUG, INFO, etc.)
            fn: Filename where the logging call was made
            lno: Line number where the logging call was made
            msg: Log message format string
            args: Arguments for log message formatting
            exc_info: Exception information tuple
            func: Function name where the logging call was made
            extra: Dictionary of extra attributes to add to the log record
            sinfo: Stack information

        Returns:
            AionLogRecord: Custom log record with request context

        Raises:
            KeyError: If extra dict attempts to overwrite protected keys
                     ('message', 'asctime', or any existing record attribute)
        """
        rv = AionLogRecord(
            name, level, fn, lno, msg,
            args, exc_info, func, sinfo
        )
        if extra is not None:
            for key in extra:
                if (key in ["message", "asctime"]) or (key in rv.__dict__):
                    raise KeyError("Attempt to overwrite %r in AionLogRecord" % key)
                rv.__dict__[key] = extra[key]
        return rv
