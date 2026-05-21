from .factory import get_logger, set_logger_factory, reset_logger_factory, _get_aion_logger_class

def __getattr__(name: str):
    if name == "AionLogger":
        return _get_aion_logger_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["get_logger", "set_logger_factory", "reset_logger_factory", "AionLogger"]
