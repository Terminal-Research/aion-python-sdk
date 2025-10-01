import logging

from aion.shared.logging.base import AionLogRecord
from .filter import AionApiContextFilter
from .manager import AionApiLogManager


class LogAionApiHandler(logging.Handler):
    """
    Lightweight handler that sends logs to async manager queue.
    Does not perform any network I/O or batching - just adds to queue.
    """

    def __init__(self):
        super().__init__()
        self.addFilter(AionApiContextFilter())

        # Get singleton manager instance (started in lifespan)
        self.manager = AionApiLogManager()

    def emit(self, record: AionLogRecord) -> None:
        """
        Add log record to async queue for processing.
        Non-blocking operation.
        """
        try:
            self.manager.add_log(record)
        except Exception:
            # Handle errors in logging gracefully
            self.handleError(record)
