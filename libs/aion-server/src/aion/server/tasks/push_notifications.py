from __future__ import annotations

import httpx
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from aion.db.postgres import AION_SCHEMA
from aion.shared.db import DbManagerProtocol
from typing import Optional


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

        sender = BasePushNotificationSender(
            httpx_client=httpx.AsyncClient(),
            config_store=config_store,
        )
        return config_store, sender

    @staticmethod
    def _create_postgres_store(db_manager: DbManagerProtocol) -> PushNotificationConfigStore:
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
        return InMemoryPushNotificationConfigStore()
