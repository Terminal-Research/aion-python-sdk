import logging
from typing import Optional

from aion.shared.settings import app_settings
from .base import AionLogger
from .handlers import LogStreamHandler, LogAionApiHandler

logging.setLoggerClass(AionLogger)


def get_logger(
        name: Optional[str] = None,
        use_stream: bool = True,
        use_aion_api: bool = True,
        level: Optional[int | str] = None
) -> logging.Logger:
    """
    Get a logger with automatic context injection and custom handlers

    Args:
        name: Logger name. If None, uses calling module's __name__
        use_stream: Whether to add console output handler
        use_aion_api: Whether to add aion api handler
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
            use_aion_api=use_aion_api,
            level=level)

    return logger


def _is_logger_configured(logger: logging.Logger) -> bool:
    """
    Check if logger already has our custom handlers configured
    """
    custom_handler_types = (LogAionApiHandler, LogStreamHandler)

    for handler in logger.handlers:
        if isinstance(handler, custom_handler_types):
            return True
    return False


def _configure_logger(
        logger: logging.Logger,
        use_stream: bool,
        use_aion_api: bool,
        level: Optional[int | str] = None
) -> None:
    """
    Configure logger with custom context-aware handlers
    """
    log_level = level or app_settings.log_level
    logger.setLevel(log_level)
    logger.propagate = False

    # Add stream handler if requested
    if use_stream:
        logger.addHandler(LogStreamHandler())

    # Add HTTP handler if requested
    if use_aion_api:
        logger.addHandler(LogAionApiHandler())
