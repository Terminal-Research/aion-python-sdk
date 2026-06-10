"""Factory for creating server loggers with context-injecting filters and custom handlers."""

import logging
from typing import Optional

from aion.server.settings import app_settings
from aion.core.settings import api_settings
from aion.core.logging.base import AionLogger
from .filters import ServerAionContextFilter
from .handlers import LogStreamHandler, AionLogstashHandler

logging.setLoggerClass(AionLogger)


def get_logger(
        name: Optional[str] = None,
        use_stream: bool = True,
        use_logstash: bool = True,
        level: Optional[int | str] = None
) -> AionLogger:
    """
    Get a logger with automatic context injection and custom handlers

    Args:
        name: Logger name. If None, uses calling module's __name__
        use_stream: Whether to add console output handler
        use_logstash: Whether to add aion api handler
        level: Logging level

    Returns:
        Configured logger with context support and custom handlers
    """
    if name is None:
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', 'unknown')

    logger = logging.getLogger(name)

    # Configure only if not already configured
    if not _is_logger_configured(logger):
        _configure_logger(
            logger=logger,
            use_stream=use_stream,
            use_logstash=use_logstash,
            level=level)
    return logger


def _is_logger_configured(logger: logging.Logger) -> bool:
    """Return True if the logger already has Aion-specific handlers attached."""
    custom_handler_types = (AionLogstashHandler, LogStreamHandler)

    for handler in logger.handlers:
        if isinstance(handler, custom_handler_types):
            return True
    return False


def _configure_logger(
        logger: logging.Logger,
        use_stream: bool,
        use_logstash: bool,
        level: Optional[int | str] = None
) -> None:
    """Attach context filter, stream handler, and optional Logstash handler to a logger."""
    log_level = level or app_settings.log_level
    logger.setLevel(log_level)
    logger.propagate = False

    # Enrich all records with tracing and server context before handlers run
    logger.addFilter(ServerAionContextFilter())

    # Add stream handler if requested
    if use_stream:
        logger.addHandler(LogStreamHandler())

    # Add HTTP handler if requested
    if use_logstash:
        logger.addHandler(AionLogstashHandler(
            host=app_settings.logstash_host,
            port=app_settings.logstash_port,
            database_path=None,  # disable db as a queue. Use inmemory queue
            transport="logstash_async.transport.HttpTransport",
            ssl_enable=False,
            ssl_verify=False,
            enable=app_settings.is_logstash_configured,
            client_id=api_settings.client_id,
            node_name=app_settings.node_name
        ))
