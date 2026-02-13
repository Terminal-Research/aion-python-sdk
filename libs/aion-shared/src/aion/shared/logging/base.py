from __future__ import annotations

import logging
from typing import Optional


class AionLogRecord(logging.LogRecord):
    """
    Custom LogRecord that captures request context information.

    This class extends the standard logging.LogRecord to automatically
    capture and store the current request context when a log record is created.

    Attributes:
        # OpenTelemetry tracing
        trace_id (Optional[str]): Active trace ID in hex format.
        trace_span_id (Optional[str]): Current span ID in hex format.
        trace_span_name (Optional[str]): Current span name.
        trace_parent_span_id (Optional[str]): Parent span ID in hex format.
        trace_baggage (Optional[dict]): W3C baggage propagated via trace context.

        # Request / transaction
        transaction_id (Optional[str]): Unique identifier of the current transaction.
        transaction_name (Optional[str]): Human-readable name of the transaction.

        # Aion deployment
        aion_distribution_id (Optional[str]): Agent distribution identifier.
        aion_version_id (Optional[str]): Agent version identifier (falls back to app_settings.version_id).
        aion_agent_environment_id (Optional[str]): Agent environment identifier.

        # HTTP request
        http_request_method (Optional[str]): HTTP method of the incoming request (e.g. GET, POST).
        http_request_target (Optional[str]): HTTP request path / target URI.

        # A2A protocol
        current_node (Optional[str]): Name of the currently executing graph node.
        task_id (Optional[str]): A2A task identifier.
        a2a_rpc_method (Optional[str]): JSON-RPC method name of the A2A call.
        a2a_task_status (Optional[str]): Current status of the A2A task.
    """
    trace_id: Optional[str]
    trace_span_id: Optional[str]
    trace_span_name: Optional[str]
    trace_parent_span_id: Optional[str]
    trace_baggage: Optional[dict]
    transaction_id: Optional[str]
    transaction_name: Optional[str]
    aion_distribution_id: Optional[str]
    aion_version_id: Optional[str]
    aion_agent_environment_id: Optional[str]
    http_request_method: Optional[str]
    http_request_target: Optional[str]
    current_node: Optional[str]
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
            request_context = get_context()
        except Exception:
            request_context = None

        try:
            trace_span_info = get_span_info()
        except Exception:
            trace_span_info = None

        # Opentelemetry tracing
        self.trace_id = getattr(trace_span_info, "trace_id_hex", None)
        self.trace_span_id = getattr(trace_span_info, "span_id_hex", None)
        self.trace_span_name = getattr(trace_span_info, "span_name", None)
        self.trace_parent_span_id = getattr(trace_span_info, "parent_span_id_hex", None)
        self.trace_baggage = request_context.trace.baggage if request_context else None

        # request context / deployment info
        self.transaction_id = request_context.trace.transaction_id if request_context else None
        self.transaction_name = request_context.transaction_name if request_context else None

        self.aion_distribution_id = request_context.aion.distribution_id if request_context else None
        self.aion_version_id = request_context.aion.version_id if request_context else app_settings.version_id
        self.aion_agent_environment_id = request_context.aion.environment_id if request_context else None
        self.http_request_method = request_context.request.method if request_context else None
        self.http_request_target = request_context.request.path if request_context else None
        self.current_node = request_context.current_node if request_context else None
        self.task_id = request_context.a2a.task_id if request_context else None
        self.a2a_rpc_method = request_context.request.jrpc_method if request_context else None
        self.a2a_task_status = request_context.a2a.task_status if request_context else None


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
