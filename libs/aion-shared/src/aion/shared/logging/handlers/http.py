import datetime
import json
import logging
import urllib
import os
from typing import Dict, Any, Optional

from ..filter import ContextFilter
from aion.shared.context import get_context, RequestContext


class LogHttpHandler(logging.Handler):
    """
    Custom HTTP handler that sends logs with context to external service
    """

    def __init__(self):
        super().__init__()
        self.url = os.getenv('LOGSTASH_ENDPOINT', 'http://localhost:8000')
        self.client_id = os.getenv('AION_CLIENT_ID', "test")
        self.method = "POST"
        self.timeout = 30
        self.addFilter(ContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        """
        Send log record to HTTP endpoint
        """
        # Skip if no Logstash endpoint or client ID configured
        if not self.url or not self.client_id:
            return

        context = get_context()
        if not context:
            return

        try:
            log_entry = self._create_log_entry(record, context)
            self._send_log_entry(log_entry)
        except Exception as e:
            # Handle errors in logging gracefully
            self.handleError(record)

    def _create_log_entry(self, record: logging.LogRecord, context: RequestContext) -> Dict[str, Any]:
        """
        Create structured log entry according to Logstash specification
        """
        # Base required fields
        log_entry = {
            '@timestamp': datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            'clientId': self.client_id,
            'logLevel': record.levelname,
            'message': record.getMessage(),

            # Host & Process metadata
            'host.name': os.getenv('NODE_NAME'),
            'process.pid': os.getpid(),

            # Application context
            'service.name': 'aion-langgraph-server',
        }

        # Optional logger name
        if record.name:
            log_entry['logger'] = record.name

        # Add context information
        logstash_context = context.get_logstash_context()
        log_entry.update(logstash_context)

        # Add exception information if present
        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            log_entry.update({
                'error.message': str(exc_value) if exc_value else 'Unknown error',
                'error.type': exc_type.__name__ if exc_type else 'Exception',
                'error.stack_trace': self.format(record) if self.formatter else str(exc_value)
            })

        # Remove None values to keep JSON clean
        return {k: v for k, v in log_entry.items() if v is not None}

    def _send_log_entry(self, log_entry: Dict[str, Any]) -> None:
        """
        Send log entry to HTTP endpoint
        """
        data = json.dumps(log_entry).encode('utf-8')

        # For debugging - remove in production
        print("Sending log entry to Logstash:", json.dumps(log_entry, indent=2))
