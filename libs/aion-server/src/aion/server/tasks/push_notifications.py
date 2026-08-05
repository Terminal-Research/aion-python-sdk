"""Factory for constructing push notification store and sender with DB or in-memory backend."""

from __future__ import annotations

import httpx
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from aion.db.postgres import AION_SCHEMA
from aion.core.db import DbManagerProtocol
from typing import Optional

from aion.server.settings import app_settings
from .authenticated_push_sender import AuthenticatedPushNotificationSender

CONNECT_TIMEOUT_SECONDS = 5.0


class PushNotificationFactory:
    """Factory for creating push notification store and sender.

    Uses DatabasePushNotificationConfigStore when db_manager is initialized,
    falls back to InMemoryPushNotificationConfigStore otherwise.
    """

    @classmethod
    def create(
            cls,
            db_manager: Optional[DbManagerProtocol] = None,
    ) -> tuple[PushNotificationConfigStore, PushNotificationSender]:
        if db_manager and db_manager.is_initialized:
            config_store: PushNotificationConfigStore = cls._create_postgres_store(db_manager)
        else:
            config_store = cls._create_memory_store()

        sender = AuthenticatedPushNotificationSender(
            httpx_client=httpx.AsyncClient(timeout=cls._build_timeout()),
            config_store=config_store,
        )
        return config_store, sender

    @staticmethod
    def _build_timeout() -> httpx.Timeout:
        """Builds the timeout policy for webhook deliveries.

        httpx defaults every phase to 5 seconds, which is a poor fit for a
        webhook: a receiver that authenticates the call and then does real work
        before answering blows through it, and the delivery fails with
        ``ReadTimeout`` even though the request was accepted. Waiting on the
        response is therefore given its own, longer budget.

        Connecting keeps the short budget. A host that cannot be reached should
        fail immediately rather than hold the delivery open, since the first
        delivery of a run is awaited in the request path.

        Returns:
            The timeout policy, with the connect phase pinned to five seconds
            and the remaining phases taken from
            ``PUSH_NOTIFICATION_TIMEOUT_SECONDS``.
        """
        return httpx.Timeout(
            app_settings.push_notification_timeout_seconds,
            connect=CONNECT_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _create_postgres_store(db_manager: DbManagerProtocol) -> PushNotificationConfigStore:
        """Build a DatabasePushNotificationConfigStore bound to the shared engine."""
        from a2a.server.tasks.database_push_notification_config_store import (
            DatabasePushNotificationConfigStore,
        )
        engine = db_manager.get_engine().execution_options(
            schema_translate_map={None: AION_SCHEMA}
        )
        return DatabasePushNotificationConfigStore(
            engine=engine,
            owner_resolver=lambda _ctx: "",
        )

    @staticmethod
    def _create_memory_store() -> PushNotificationConfigStore:
        """Return an in-memory push notification config store as a fallback."""
        return InMemoryPushNotificationConfigStore()
