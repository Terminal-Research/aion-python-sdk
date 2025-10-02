import logging

from aion.shared.logging.base import AionLogRecord


class AionApiContextFilter(logging.Filter):
    def filter(self, record: AionLogRecord) -> bool:
        if record.levelno < logging.INFO:
            return False

        if not hasattr(record, "request_context"):
            return False

        if not record.request_context:
            return False
        return True
