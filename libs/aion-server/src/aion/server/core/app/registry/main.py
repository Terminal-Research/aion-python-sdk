"""Core registry for custom app-level extensions from developers.

This module provides AppRegistry - a singleton registry that allows developers
to register custom FastAPI routers and other app-level extensions.
"""

import threading
from typing import List, TYPE_CHECKING

from aion.shared.logging import get_logger
from aion.shared.metaclasses import Singleton

from .mixins import RouterRegistryMixin

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

logger = get_logger()


class AppRegistry(RouterRegistryMixin, metaclass=Singleton):
    """Singleton registry for custom app-level extensions.

    This registry allows external developers to register custom FastAPI routers
    and other app-level extensions that will be automatically integrated into
    the main application during initialization.

    The registry uses mixins to organize functionality by concern:
    - RouterRegistryMixin: Handles FastAPI router registration
    """

    def __init__(self):
        """Initialize the registry with empty collections."""
        self._lock = threading.Lock()
        self._routers: List["APIRouter"] = []
        logger.debug("AppRegistry initialized")

    def apply_to_app(self, app: "FastAPI") -> None:
        """Apply all registered extensions to the FastAPI application.

        This method integrates all registered routers and other extensions
        into the provided FastAPI application instance. It should be called
        during application initialization after all extensions have been
        registered.

        Args:
            app: FastAPI application instance to integrate extensions into
        """
        with self._lock:
            self._apply_routers(app)

    def clear(self) -> None:
        """Clear all registered extensions.

        Warning:
            This will remove all registered extensions. Use with caution.
        """
        with self._lock:
            self._clear_routers()
