from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogRecord

__all__ = [
    "replace_uvicorn_loggers",
    "replace_logstash_loggers",
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
