"""Extension routing: the ExtensionTaskHandler contract, the discovery
registry, and one subpackage per concrete implementation (e.g. `evolution`).
"""

from .availability import ExtensionAvailability
from .base import (
    ExtensionTaskHandler,
    discover_extension_task_handlers,
)
from .errors import ExtensionPreflightError

__all__ = [
    "ExtensionAvailability",
    "ExtensionPreflightError",
    "ExtensionTaskHandler",
    "discover_extension_task_handlers",
]
