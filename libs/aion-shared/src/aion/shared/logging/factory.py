import logging
from typing import Optional

from .handlers import LogStreamHandler, LogHttpHandler


def get_logger(
        name: Optional[str] = None,
        use_stream: bool = True,
        use_http: bool = True
) -> logging.Logger:
    """
    Get a logger with automatic context injection and custom handlers

    Args:
        name: Logger name. If None, uses calling module's __name__
        use_stream: Whether to add console output handler
        use_http: Whether to add http handler

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
        _configure_logger(logger, use_stream, use_http)

    return logger


def _is_logger_configured(logger: logging.Logger) -> bool:
    """
    Check if logger already has our custom handlers configured
    """
    custom_handler_types = (LogHttpHandler, LogStreamHandler)

    for handler in logger.handlers:
        if isinstance(handler, custom_handler_types):
            return True
    return False


def _configure_logger(
        logger: logging.Logger,
        use_stream: bool,
        use_http: bool
) -> None:
    """
    Configure logger with custom context-aware handlers
    """
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Add stream handler if requested
    if use_stream:
        logger.addHandler(LogStreamHandler())

    # Add HTTP handler if requested
    if use_http:
        http_handler = LogHttpHandler()
        # Set higher level for HTTP to avoid spam
        http_handler.setLevel(logging.INFO)
        logger.addHandler(http_handler)
