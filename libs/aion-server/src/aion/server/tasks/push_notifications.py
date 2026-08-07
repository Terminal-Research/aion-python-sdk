"""Factory for constructing push notification store and sender with DB or in-memory backend."""

from __future__ import annotations

import logging

import httpx
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from aion.db.postgres import AION_SCHEMA
from aion.core.db import DbManagerProtocol
from typing import Optional

from aion.server.settings import app_settings
from .authenticated_push_sender import AuthenticatedPushNotificationSender

logger = logging.getLogger(__name__)

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
        """Build a DatabasePushNotificationConfigStore bound to the shared engine.

        A stored configuration is the callback URL plus the credentials the
        receiver expects to see on the webhook call. Handing the store the
        deployment's encryption key makes it Fernet-encrypt that payload before
        it reaches the ``config_data`` column; without one the credentials are
        persisted as plaintext JSON. The key is opt-in because encryption is not
        free to adopt: see the rotation note below.

        Returns:
            The database-backed config store, encrypting at rest when
            ``ENCRYPTION_KEY`` is configured.
        """
        from a2a.server.tasks.database_push_notification_config_store import (
            DatabasePushNotificationConfigStore,
        )
        engine = db_manager.get_engine().execution_options(
            schema_translate_map={None: AION_SCHEMA}
        )
        encryption_key = app_settings.encryption_key

        # TODO(encryption): support rotating ENCRYPTION_KEY. The SDK store takes
        #  a single Fernet key and reads every row with it, so changing the
        #  value strands configs written under the old one: decryption fails, the
        #  plaintext-JSON fallback fails too, and ``get_info_for_dispatch``
        #  raises out of ``send_notification`` — deliveries break for tasks
        #  registered before the change, rather than degrading. Enabling
        #  encryption on an existing database is safe (rows written as plaintext
        #  still parse through that same fallback); it is only key *changes*
        #  that are unsupported. Fix by passing a MultiFernet built from a
        #  primary plus retired keys via ``core_to_model_conversion`` /
        #  ``model_to_core_conversion``, or by re-encrypting the table on
        #  rotation.
        if encryption_key:
            logger.info(
                "Push-notification configs will be encrypted at rest with "
                "ENCRYPTION_KEY."
            )
        else:
            logger.warning(
                "Push-notification configs will be stored unencrypted, including "
                "any webhook credentials they carry. Set ENCRYPTION_KEY to "
                "encrypt them at rest."
            )

        return DatabasePushNotificationConfigStore(
            engine=engine,
            encryption_key=encryption_key,
            owner_resolver=lambda _ctx: "",
        )

    @staticmethod
    def _create_memory_store() -> PushNotificationConfigStore:
        """Return an in-memory push notification config store as a fallback."""
        return InMemoryPushNotificationConfigStore()
