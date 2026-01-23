"""Router registry mixin for managing custom FastAPI routers.

This module provides RouterRegistryMixin that handles registration and
retrieval of custom FastAPI routers.
"""

from __future__ import annotations

from typing import List, Set, TYPE_CHECKING

from aion.shared.logging import get_logger

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI
    from threading import Lock

logger = get_logger()


class RouterRegistryMixin:
    """Mixin for managing FastAPI router registration.

    This mixin provides methods to register, retrieve, and manage custom
    FastAPI routers that will be integrated into the main application.

    Attributes are expected to be provided by the parent class:
        _lock: threading.Lock for thread-safe operations
        _routers: List[APIRouter] storage for registered routers
    """
    _lock: Lock
    _routers: List[APIRouter]

    def add_router(self, router: "APIRouter") -> None:
        """Register a FastAPI router.

        The router will be included in the main FastAPI application during
        initialization. Multiple routers can be registered and they will be
        included in the order they were registered.

        If the same router instance is registered multiple times, it will be
        skipped to prevent duplicates.

        Args:
            router: FastAPI APIRouter instance to register
        """
        with self._lock:
            if router in self._routers:
                return

            self._routers.append(router)
            logger.debug(f"Registered router with prefix: {router.prefix or '/'}")

    def get_routers(self) -> List["APIRouter"]:
        """Get all registered routers.

        Returns a copy of the routers list to prevent external modifications.

        Returns:
            List of registered APIRouter instances
        """
        with self._lock:
            return self._routers.copy()

    @staticmethod
    def _detect_reserved_paths(app: FastAPI) -> Set[str]:
        """Detect reserved paths from the FastAPI application.

        This method scans all existing routes in the application and returns
        their paths. These paths are considered reserved and cannot be
        overridden by custom routers.

        Args:
            app: FastAPI application instance

        Returns:
            Set of reserved path strings
        """
        reserved_paths = set()
        for route in app.routes:
            if hasattr(route, "path"):
                reserved_paths.add(route.path)

        logger.info(
            f"Detected {len(reserved_paths)} reserved path(s) from application"
        )
        return reserved_paths

    @staticmethod
    def _has_route_conflicts(router: "APIRouter", reserved_paths: Set[str]) -> bool:
        """Check if router has conflicting routes with reserved app paths.

        This method checks if any route path in the router (including its prefix)
        conflicts with reserved application paths. Path conflicts are checked
        regardless of HTTP method - if a path is reserved, no custom router can
        use it with any method.

        Args:
            router: Router to check for conflicts
            reserved_paths: Set of paths reserved by the application

        Returns:
            True if conflicts found, False otherwise
        """
        router_prefix = router.prefix or ""

        for route in router.routes:
            if hasattr(route, "path"):
                full_path = router_prefix + route.path
                if full_path in reserved_paths:
                    logger.debug(
                        f"Conflict detected: path {full_path} is reserved by application"
                    )
                    return True
        return False

    def _apply_routers(self, app: FastAPI) -> None:
        """Apply all registered routers to the FastAPI application.

        This internal method is called by the main apply_to_app() method to
        integrate registered routers. The lock should be acquired by the caller.

        Detects reserved paths from the application at the beginning, then checks
        each router for conflicts with reserved paths. If a router contains routes
        that conflict with reserved paths (same path, any method), a warning is
        logged and the router is skipped.

        Args:
            app: FastAPI application instance to add routers to
        """
        # Detect reserved paths from the application
        reserved_paths = self._detect_reserved_paths(app)

        applied_count = 0
        for router in self._routers:
            if self._has_route_conflicts(router, reserved_paths):
                logger.warning(
                    f"Router with prefix '{router.prefix or '/'}' has conflicting routes "
                    f"with reserved application paths. Skipping registration to prevent route override."
                )
                continue

            app.include_router(router)
            logger.debug(f"Applied router with prefix: {router.prefix or '/'}")
            applied_count += 1

        if applied_count:
            logger.info(f"Applied {applied_count} router(s) to FastAPI app")

    def _clear_routers(self) -> None:
        """Clear all registered routers.

        This internal method is called by the main clear() method to reset
        the router registry state. The lock should be acquired by the caller.

        Returns:
            None. Logs the number of cleared routers.
        """
        router_count = len(self._routers)
        self._routers.clear()
        logger.debug(f"Cleared {router_count} registered router(s)")
