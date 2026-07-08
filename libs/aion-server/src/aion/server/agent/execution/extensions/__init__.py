"""Extension routing: the ExtensionTaskHandler contract, the discovery
registry, and one subpackage per concrete implementation (e.g. `evolution`).
"""

from .base import (
    ROUTED_EXTENSION_METADATA_KEY,
    ExtensionTaskHandler,
    discover_extension_task_handlers,
)

__all__ = [
    "ExtensionTaskHandler",
    "discover_extension_task_handlers",
    "ROUTED_EXTENSION_METADATA_KEY",
]
