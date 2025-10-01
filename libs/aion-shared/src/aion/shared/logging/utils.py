from __future__ import annotations

import datetime
import os
import traceback
from typing import Dict, Any, Optional
from typing import TYPE_CHECKING

from aion.shared.context import RequestContext

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogRecord

__all__ = [
    "replace_uvicorn_loggers",
    "create_logstash_log_entry"
]


def replace_uvicorn_loggers():
    """
    Replace uvicorn's default loggers with custom Aion loggers.

    This function reconfigures the logging for uvicorn, starlette, and fastapi
    to use the Aion logging system instead of their default loggers.
    Enables stream output and disables Aion API logging for these loggers.

    Note:
        This should be called during application initialization before
        uvicorn starts processing requests.
    """
    from .factory import get_logger

    for logger_name in ("uvicorn.access", "uvicorn", "starlette", "fastapi"):
        get_logger(logger_name, use_stream=True, use_aion_api=False)


def create_logstash_log_entry(
        record: AionLogRecord,
        request_context: RequestContext,
        client_id: str,
        node_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a structured log entry formatted for Logstash ingestion.

    Generates a dictionary containing all required and optional fields
    according to Logstash specification, including timestamp, log level,
    message, host information, service metadata, and exception details.

    Args:
        record: AionLogRecord instance containing the log information
        request_context: Current request context with additional metadata
        client_id: Unique identifier for the client making the request
        node_name: Optional name of the node/host where the log originated.
                  If None, host.name field will be set to None.

    Returns:
        Dict[str, Any]: Structured log entry with the following keys:
            - @timestamp: ISO 8601 formatted UTC timestamp
            - clientId: Client identifier
            - logLevel: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            - message: Formatted log message
            - host.name: Node/host name
            - process.pid: Process ID
            - service.name: Service identifier
            - logger: Logger name (optional, only if present in record)
            - error.message: Error message (only if exception present)
            - error.type: Exception type name (only if exception present)
            - error.stack_trace: Full stack trace (only if exception present)
            - Additional fields from request_context.get_aion_log_context()
    """
    log_entry = {
        '@timestamp': datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        'clientId': client_id,
        'logLevel': record.levelname,
        'message': record.getMessage(),

        # Host & Process metadata
        'host.name': node_name,
        'process.pid': os.getpid(),

        # Application context
        'service.name': "aion-langgraph-server",
        'error.message': None,
        'error.type': None,
        'error.stack_trace': None
    }

    # Optional logger name
    if record.name:
        log_entry['logger'] = record.name

    # Add context information
    logstash_context = request_context.get_aion_log_context()
    log_entry.update(logstash_context)

    # Add exception information if present
    if record.exc_info:
        exc_type, exc_value, exc_traceback = record.exc_info
        log_entry.update({
            'error.message': str(exc_value) if exc_value else 'Unknown error',
            'error.type': exc_type.__name__ if exc_type else 'Exception',
            'error.stack_trace': ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        })
    return log_entry
