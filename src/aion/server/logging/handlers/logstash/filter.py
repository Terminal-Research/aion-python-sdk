"""Record filter deciding which log records are worth shipping to Logstash."""

import logging

from aion.core.logging.base import AionLogRecord

__all__ = ["AionLogstashFilter"]


class AionLogstashFilter(logging.Filter):
    """Filter log records for Logstash processing.

    Only allows records with INFO level or higher that contain
    valid request context information.
    """

    def filter(self, record: AionLogRecord) -> bool:
        if not self._validate_log_level(record):
            return False

        if not any((
                self._validate_deployment(record),
                self._validate_tracing(record))
        ):
            return False

        return True

    @staticmethod
    def _validate_log_level(record: AionLogRecord):
        return record.levelno > logging.DEBUG

    @staticmethod
    def _validate_deployment(record: AionLogRecord):
        return bool(
            getattr(record, 'aion_distribution_id', None) or
            getattr(record, 'aion_version_id', None)
        )

    @staticmethod
    def _validate_tracing(record: AionLogRecord):
        return bool(getattr(record, 'trace_id', None))
