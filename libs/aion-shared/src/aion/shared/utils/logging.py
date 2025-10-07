from __future__ import annotations

import logging
import datetime
import os
import traceback
from typing import Dict, Any, Optional
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogRecord
    from aion.shared.opentelemetry.tracing import SpanInfo
    from aion.shared.context.request_context import RequestContext

__all__ = [
    "replace_uvicorn_loggers",
    "replace_logstash_loggers",
    "create_logstash_log_entry"
]


def replace_uvicorn_loggers(suppress_startup_logs: bool = False):
    """
    Replace uvicorn's default loggers with custom Aion loggers.

    This function reconfigures the logging for uvicorn, starlette, and fastapi
    to use the Aion logging system instead of their default loggers.
    Enables both stream output and Aion API logging for incoming request tracking.

    Args:
        suppress_startup_logs: If True, reduces verbosity of startup/shutdown logs
                              to avoid cluttering application logs

    Note:
        This should be called during application initialization before
        uvicorn starts processing requests.
    """
    from aion.shared.logging.factory import get_logger

    # uvicorn.access logs incoming HTTP requests (e.g., "GET / HTTP/1.1" 200 OK)
    get_logger("uvicorn.access", use_stream=True, use_logstash=True)

    # uvicorn logs server events (startup, shutdown, etc.)
    uvicorn_logger = get_logger("uvicorn", use_stream=True, use_logstash=False)

    # uvicorn.error - handles startup/shutdown messages
    uvicorn_error_logger = get_logger("uvicorn.error", use_stream=True, use_logstash=False)

    # Optional: reduce verbosity of infrastructure logs
    if suppress_startup_logs:
        uvicorn_logger.setLevel(logging.WARNING)
        uvicorn_error_logger.setLevel(logging.WARNING)

    # starlette and fastapi logs (less verbose, mainly for errors)
    get_logger("starlette", use_stream=True, use_logstash=False)
    get_logger("fastapi", use_stream=True, use_logstash=False)


def replace_logstash_loggers():
    """Configure logstash-related loggers to use stream output only, preventing circular logging."""
    from aion.shared.logging.factory import get_logger
    get_logger("LogProcessingWorker", use_stream=True, use_logstash=False)
    get_logger("logstash_async.transport", use_stream=True, use_logstash=False)
    get_logger("logstash_async.memory_cache", use_stream=True, use_logstash=False)


def create_logstash_log_entry(
        record: AionLogRecord,
        client_id: str,
        node_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a structured log entry formatted for Logstash ingestion.

    Generates a dictionary containing all required and optional fields
    according to Logstash specification, including timestamp, log level,
    message, host information, service metadata, tracing context, and exception details.

    Args:
        record: AionLogRecord instance containing the log information, including
                optional trace_span_info and request_context attributes
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
    log_entry = {
        '@timestamp': datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        'clientId': client_id,
        'logLevel': record.levelname,
        'message': record.getMessage(),

        # Host & Process metadata
        'host.name': node_name,
        'process.pid': os.getpid(),

        "trace.id": None,
        "span.id": None,
        "span.name": None,
        "parent.span.id": None,

        # Context information
        "transaction.id": None,
        "transaction.name": None,
        "tags": {},

        # Application context
        'service.name': "aion-langgraph-server",
        'error.message': None,
        'error.type': None,
        'error.stack_trace': None
    }

    span_info: Optional[SpanInfo] = getattr(record, "trace_span_info", None)
    if span_info:
        log_entry.update({
            "trace.id": span_info.trace_id_hex,
            "span.id": span_info.span_id_hex,
            "span.name": span_info.span_name,
            "parent.span.id": span_info.parent_span_id_hex,
        })

    # Optional logger name
    if record.name:
        log_entry['logger'] = record.name

    # Add context information
    request_context: Optional[RequestContext] = getattr(record, "request_context", None)
    if request_context:
        log_entry.update(request_context.get_aion_log_context())

    # Add exception information if present
    if record.exc_info:
        exc_type, exc_value, exc_traceback = record.exc_info
        log_entry.update({
            'error.message': str(exc_value) if exc_value else 'Unknown error',
            'error.type': exc_type.__name__ if exc_type else 'Exception',
            'error.stack_trace': ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        })
    return log_entry
