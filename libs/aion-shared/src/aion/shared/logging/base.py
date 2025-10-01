import logging
from typing import Optional

from ..context import RequestContext, get_context


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

    def __init__(self, *args, **kwargs):
        """
        Initialize AionLogRecord with request context.

        Args:
            *args: Positional arguments passed to logging.LogRecord
            **kwargs: Keyword arguments passed to logging.LogRecord
        """
        super().__init__(*args, **kwargs)
        try:
            self.request_context = get_context()
        except Exception:
            self.request_context = None


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
