"""Mixins for AppRegistry functionality.

This package provides mixins that compose the AppRegistry functionality.
Each mixin handles a specific concern (routers, middleware, hooks, etc).
"""

from .router import RouterRegistryMixin

__all__ = [
    "RouterRegistryMixin",
]
