"""Factory for constructing push notification store and sender with DB or in-memory backend."""

from __future__ import annotations

import logging

import httpx
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.utils.push_url_validator import validate_push_notification_url
from aion.db.postgres import AION_SCHEMA
from aion.core.db import DbManagerProtocol
from collections.abc import Awaitable, Callable
from typing import Optional

from aion.server.auth import is_hosted
from aion.server.settings import app_settings
from .push_sender import AionPushNotificationSender

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0

PushUrlValidator = Callable[[str], Awaitable[bool]]


def push_url_validator() -> Optional[PushUrlValidator]:
    """The check a push URL must pass on this server, or ``None`` for none.

    On a server the Aion platform hosts (``DEPLOYMENT_ID`` set) it is
    a2a-sdk's ``validate_push_notification_url``: only an ``http`` or
    ``https`` URL whose host resolves to public addresses is accepted, so a
    client cannot have the server post to the deployment's own network or to
    the cloud metadata endpoint. Elsewhere every URL is accepted - a server
    run locally, or deployed by its owner, delivers to ``localhost`` and to
    private networks as its webhooks need.

    The request handler applies it when a config is created, inline in a
    ``SendMessage`` too, and the sender before every delivery, so a config
    stored before the server was hosted is skipped with a warning.
    """
    return validate_push_notification_url if is_hosted() else None


class PushNotificationFactory:
    """Factory for creating push notification store and sender.

    Uses DatabasePushNotificationConfigStore when db_manager is initialized,
    falls back to InMemoryPushNotificationConfigStore otherwise.
    """

    @classmethod
    def create(
            cls,
            db_manager: Optional[DbManagerProtocol] = None,
            owner_resolver: OwnerResolver = resolve_user_scope,
            push_url_validator: Optional[PushUrlValidator] = None,
    ) -> tuple[PushNotificationConfigStore, PushNotificationSender]:
        """Build the config store and the sender that delivers from it.

        Args:
            db_manager: The database the configs live in when it is initialized;
                in memory otherwise.
            owner_resolver: The agent's resolver, so a task's configs belong to
                the task's owner when a client reads or deletes them. Delivery
                reads them by task id alone, across owners.
            push_url_validator: The check every delivery's URL must pass
                first; ``None`` delivers to any URL. See
                ``push_url_validator()``.
        """
        if db_manager and db_manager.is_initialized:
            config_store: PushNotificationConfigStore = cls._create_postgres_store(
                db_manager, owner_resolver
            )
        else:
            config_store = cls._create_memory_store(owner_resolver)

        sender = AionPushNotificationSender(
            httpx_client=httpx.AsyncClient(timeout=cls._build_timeout()),
            config_store=config_store,
            push_url_validator=push_url_validator,
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
    def _create_postgres_store(
            db_manager: DbManagerProtocol,
            owner_resolver: OwnerResolver,
    ) -> PushNotificationConfigStore:
        """Build the database config store bound to the shared engine.

        A stored configuration is the callback URL plus the credentials the
        receiver expects to see on the webhook call. Handing the store the
        deployment's encryption keys makes it Fernet-encrypt that payload with
        the first key before it reaches the ``config_data`` column, and read a
        config written under any of them; without keys the credentials are
        persisted as plaintext JSON.

        Returns:
            The database-backed config store, encrypting at rest when
            ``ENCRYPTION_KEY`` is configured. Its table is created by the SDK
            migrations.
        """
        from .push_config_store import AionDatabasePushNotificationConfigStore
        engine = db_manager.get_engine().execution_options(
            schema_translate_map={None: AION_SCHEMA}
        )
        encryption_keys = app_settings.encryption_keys

        # TODO(push-key-reencryption): a config keeps the key it was written
        #  under; see docs/development/open-items.md.
        if encryption_keys:
            logger.info(
                "Push-notification configs will be encrypted at rest with "
                "ENCRYPTION_KEY (%d key(s)).",
                len(encryption_keys),
            )
        else:
            logger.warning(
                "Push-notification configs will be stored unencrypted, including "
                "any webhook credentials they carry. Set ENCRYPTION_KEY to "
                "encrypt them at rest."
            )

        return AionDatabasePushNotificationConfigStore(
            engine=engine,
            encryption_keys=encryption_keys,
            owner_resolver=owner_resolver,
        )

    @staticmethod
    def _create_memory_store(owner_resolver: OwnerResolver) -> PushNotificationConfigStore:
        """Return an in-memory push notification config store as a fallback."""
        return InMemoryPushNotificationConfigStore(owner_resolver=owner_resolver)
