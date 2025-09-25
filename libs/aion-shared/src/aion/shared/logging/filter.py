import logging

from aion.shared.context import get_context


class ContextFilter(logging.Filter):
    """
    Filter that automatically adds context to all log records
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Get current context and add to log record
        record.request_context = get_context()
        return True
