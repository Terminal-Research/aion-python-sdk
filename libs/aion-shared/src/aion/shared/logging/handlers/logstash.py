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
import datetime
import json
import logging
import os
import traceback

from aion.shared.logging.base import AionLogRecord
from logstash_async.formatter import LogstashFormatter
from logstash_async.handler import AsynchronousLogstashHandler


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
        if not any((
            record.aion_distribution_id,
            record.aion_version_id
        )):
            return False
        return True

    @staticmethod
    def _validate_tracing(record: AionLogRecord):
        return bool(record.trace_id)


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
        """Create a structured log entry formatted for Logstash ingestion.

        Generates a dictionary containing all required and optional fields
        according to Logstash specification, including timestamp, log level,
        message, host information, service metadata, tracing context, and exception details.

        Returns:
            Dict[str, Any]: Structured log entry with the following keys:
                - @timestamp: ISO 8601 formatted UTC timestamp
                - clientId: Client identifier
                - logLevel: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                - message: Formatted log message
                - host.name: Node/host name
                - process.pid: Process ID
                - service.name: Service identifier (defaults to "aion-langgraph-server")
                - logger: Logger name (optional, only if present in record)
                - trace.id: Trace ID in hex format (from SpanInfo if available)
                - span.id: Span ID in hex format (from SpanInfo if available)
                - span.name: Span name (from SpanInfo if available)
                - parent.span.id: Parent span ID in hex format (from SpanInfo if available)
                - transaction.id: Transaction identifier (from RequestContext if available)
                - transaction.name: Transaction name (from RequestContext if available)
                - tags: Additional tags dictionary (from RequestContext if available)
                - error.message: Error message (only if exception present)
                - error.type: Exception type name (only if exception present)
                - error.stack_trace: Full stack trace (only if exception present)
        """
        message = {
            '@timestamp': datetime.datetime.fromtimestamp(
                record.created,
                tz=datetime.timezone.utc
            ).strftime('%Y-%m-%dT%H:%M:%S') + f'.{int(record.msecs):03d}Z',
            'clientId': self._client_id,
            'logLevel': record.levelname,
            'message': record.getMessage(),
            'logger': record.name,

            # Host & Process metadata
            'host.name': self._node_name,
            'process.pid': os.getpid(),

            "trace.id": record.trace_id,
            "span.id": record.trace_span_id,
            "span.name": record.trace_span_name,
            "parent.span.id": record.trace_patent_span_id,

            # Context information
            "transaction.id": record.transaction_id,
            "transaction.name": record.transaction_name,
            "tags": {
                "aion.distribution.id": record.aion_distribution_id,
                "aion.version.id": record.aion_version_id,
                "aion.agentEnvironment.id": record.aion_agent_environment_id,
                "http.method": record.http_request_method,
                "http.target": record.http_request_target,
                "langgraph.node": record.langgraph_node
            },

            # Application context
            'service.name': "aion-langgraph-server",
            'error.message': None,
            'error.type': None,
            'error.stack_trace': None
        }
        # Add exception information if present
        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            message.update({
                'error.message': str(exc_value) if exc_value else 'Unknown error',
                'error.type': exc_type.__name__ if exc_type else 'Exception',
                'error.stack_trace': ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            })

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
