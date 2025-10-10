from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aion.shared.context import RequestContext
    from aion.shared.opentelemetry.tracing import SpanInfo


class AionLogRecord(logging.LogRecord):
    """
    Custom LogRecord that captures request context information.

    This class extends the standard logging.LogRecord to automatically
    capture and store the current request context when a log record is created.

    Attributes:
        request_context: Optional RequestContext object containing information
                        about the current request. Will be None if context
                        cannot be retrieved.
    """
    request_context: Optional[RequestContext]
    trace_span_info: Optional[SpanInfo]
    distribution_id: Optional[str]
    version_id: Optional[str]

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
        self.trace_patent_span_id = getattr(trace_span_info, "parent_span_id_hex", None)

        # request context / deployment info
        self.transaction_id = getattr(request_context, "transaction_id", None)
        self.transaction_name = getattr(request_context, "transaction_name", None)

        self.aion_distribution_id = getattr(request_context, "aion_distribution_id", app_settings.distribution_id)
        self.aion_version_id = getattr(request_context, "aion_version_id", app_settings.version_id)
        self.aion_agent_environment_id = getattr(request_context, "aion_agent_environment_id", None)
        self.http_request_method = getattr(request_context, "request_method", None)
        self.http_request_target = getattr(request_context, "request_path", None)
        self.langgraph_node = getattr(request_context, "langgraph_current_node", None)

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
