"""A2A protocol server for agents written with a supported framework."""

from aion.core.utils.optional_deps import (
    MissingOptionalDependency,
    is_own_module,
    missing_server_extra_error,
)

# This package ships in every wheel; the ASGI stack, the a2a-sdk server extras
# and the database driver it is built on only arrive with a server extra.
# Without them the import fails deep inside a third-party module, on a name
# that means nothing to whoever wrote the agent.
try:
    from .core.app.registry import app_registry
    from .server import run_server
except MissingOptionalDependency as exc:
    # aion.db.postgres guards its own imports and gets there first when the
    # database libraries are what is missing. Same extras, so only the name
    # changes: the reader imported aion.server, and that is what the line says.
    raise missing_server_extra_error("aion.server", exc) from exc
except ModuleNotFoundError as exc:
    if is_own_module(exc.name):
        raise
    raise missing_server_extra_error("aion.server", exc) from exc

__all__ = ["run_server", "app_registry"]
