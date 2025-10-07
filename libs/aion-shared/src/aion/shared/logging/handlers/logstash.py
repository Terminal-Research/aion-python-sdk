"""
Logstash integration module for Aion logging system.

This module provides custom Logstash handler, formatter, and filter classes
for sending structured logs to Logstash asynchronously. It extends the
python-logstash-async library with Aion-specific functionality including request
context filtering and custom log entry formatting.

Classes:
    AionLogstashFilter: Filters log records based on level and request context.
    AionLogstashFormatter: Formats log records into Logstash-compatible JSON.
    AionLogstashHandler: Asynchronous handler for sending logs to Logstash.
"""

import json
import logging

from logstash_async.formatter import LogstashFormatter
from logstash_async.handler import AsynchronousLogstashHandler

from aion.shared.logging.base import AionLogRecord
from aion.shared.utils import create_logstash_log_entry


class AionLogstashFilter(logging.Filter):
    """Filter log records for Logstash processing.

    Only allows records with INFO level or higher that contain
    valid request context information.
    """

    def filter(self, record: AionLogRecord) -> bool:
        if record.levelno < logging.INFO:
            return False

        if not hasattr(record, "request_context"):
            return False

        if not record.request_context:
            return False
        return True


class AionLogstashFormatter(LogstashFormatter):
    """Format log records into Logstash-compatible JSON format.

    Args:
        client_id: Unique identifier for the client.
        node_name: Name of the node generating the logs.
        **kwargs: Additional arguments passed to LogstashFormatter.
    """

    def __init__(self, client_id: str, node_name: str, **kwargs):
        super().__init__(**kwargs)
        self._client_id = client_id
        self._node_name = node_name

    def format(self, record: AionLogRecord) -> str:
        """Format a log record as JSON string.

        Args:
            record: The log record to format.

        Returns:
            JSON-formatted log entry string.
        """
        message = create_logstash_log_entry(
            record=record,
            client_id=self._client_id,
            node_name=self._node_name)
        return json.dumps(message)


class AionLogstashHandler(AsynchronousLogstashHandler):
    """Asynchronous handler for sending logs to Logstash.

    Automatically configures the handler with AionLogstashFormatter
    and AionLogstashFilter.

    Args:
        client_id: Unique identifier for the client.
        node_name: Name of the node generating the logs.
        **kwargs: Additional arguments passed to AsynchronousLogstashHandler.
    """

    def __init__(self, client_id: str, node_name: str, **kwargs):
        super().__init__(**kwargs)
        self.setFormatter(AionLogstashFormatter(client_id=client_id, node_name=node_name))
        self.addFilter(AionLogstashFilter())
