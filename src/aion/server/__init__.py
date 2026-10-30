"""A2A protocol server for agents written with a supported framework.

Entry points load on access so logging can be configured before importing the
application and validating its database settings.
"""

from typing import TYPE_CHECKING

from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)

if TYPE_CHECKING:
    from .core.app.registry import app_registry
    from .server import run_server

__all__ = ["run_server", "app_registry"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        if name == "run_server":
            from .server import run_server
            value = run_server
        else:
            from .core.app.registry import app_registry
            value = app_registry
    except MissingOptionalDependency as exc:
        raise missing_server_extra_error("aion.server", exc) from exc
    except ModuleNotFoundError as exc:
        if is_own_module(exc.name):
            raise
        raise missing_server_extra_error("aion.server", exc) from exc

    globals()[name] = value
    return value
