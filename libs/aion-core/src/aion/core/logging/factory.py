import inspect
import logging
from typing import Any, Callable, Optional, Type

_logger_factory: Optional[Callable[[Optional[str]], Any]] = None
_resolved: bool = False
_aion_logger_class: Optional[Type] = None


def _get_aion_logger_class() -> Type:
    """Lazily load AionLogger to avoid circular imports."""
    global _aion_logger_class
    if _aion_logger_class is not None:
        return _aion_logger_class

    try:
        from aion.server.logging import AionLogger
        _aion_logger_class = AionLogger
        return AionLogger
    except ImportError:
        _aion_logger_class = logging.Logger
        return logging.Logger


def get_logger(name: Optional[str] = None) -> Any:
    """Return a logger for the given name.

    If name is None, automatically detects the caller's module name.
    On the first call, lazily attempts to import aion.server.logging and use
    its get_logger as the factory. If aion-server is not installed, falls back
    to stdlib logging.getLogger. The resolved factory is cached — subsequent
    calls skip the import attempt entirely.
    """
    global _logger_factory, _resolved
    if name is None:
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            name = frame.f_back.f_globals.get('__name__')

    if not _resolved:
        try:
            from aion.server.logging import get_logger as _server_get_logger
            _logger_factory = _server_get_logger
            # Also ensure AionLogger class is loaded for setLoggerClass
            _get_aion_logger_class()
        except ImportError:
            pass
        _resolved = True

    if _logger_factory is not None:
        return _logger_factory(name)
    return logging.getLogger(name)


def set_logger_factory(factory: Callable[[Optional[str]], Any]) -> None:
    """Override the logger factory explicitly, skipping auto-discovery."""
    global _logger_factory, _resolved
    _logger_factory = factory
    _resolved = True


def reset_logger_factory() -> None:
    """Reset factory and re-enable auto-discovery — intended for tests only."""
    global _logger_factory, _resolved
    _logger_factory = None
    _resolved = False
