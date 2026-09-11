"""Configuration error exceptions.

Kept as an import path of its own: ``ConfigurationError`` is defined with the
rest of the SDK's public hierarchy in :mod:`aion.core.exceptions`, and callers
who already import it from here keep working.
"""

from aion.core.exceptions import ConfigurationError

__all__ = ["ConfigurationError"]
