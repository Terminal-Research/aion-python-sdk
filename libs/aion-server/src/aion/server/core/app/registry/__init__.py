"""Registry package for custom app-level extensions.

This package provides the AppRegistry singleton that allows developers to
register custom FastAPI routers and other app-level extensions.
"""

from .main import AppRegistry

# Create singleton instance for convenient access
app_registry = AppRegistry()

__all__ = [
    "AppRegistry",
    "app_registry",
]
