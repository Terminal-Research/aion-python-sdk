"""The PostgreSQL push notification config store, with a table setup servers can share."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from a2a.server.tasks.database_push_notification_config_store import (
    DatabasePushNotificationConfigStore,
)
from sqlalchemy import text

__all__ = ["AionDatabasePushNotificationConfigStore", "PUSH_CONFIG_SETUP_LOCK_KEY"]

# a2a-sdk creates its push config table itself: it checks whether the table
# exists and then creates it. In cluster mode every server of an agent shares
# one database, so servers preparing the table together on a fresh database
# would all create it, and all but one would fail on PostgreSQL's catalog
# constraints. They take turns under this session-level advisory lock,
# distinct from the SDK migration lock so neither setup waits on the other.
PUSH_CONFIG_SETUP_LOCK_KEY = 7_382_194_614


class AionDatabasePushNotificationConfigStore(DatabasePushNotificationConfigStore):
    """a2a-sdk's database config store with several encryption keys and a shared setup.

    a2a-sdk takes a single Fernet key. This store takes the deployment's list
    of keys and gives a2a-sdk a ``MultiFernet`` in its place: the first key
    encrypts every config written, and a config written under any key in the
    list is still read. a2a-sdk touches the key only through its
    ``encrypt``/``decrypt`` calls, which ``MultiFernet`` provides with the
    same signatures and the same ``InvalidToken`` on failure.

    A config encrypted under a key no longer in the list cannot be read.
    a2a-sdk logs it and leaves it out of what it returns, so delivery goes on
    to the task's other configs.

    The server calls :meth:`initialize` while it starts, so the table exists
    before the first request reaches the store rather than being created by it.
    """

    def __init__(self, *args: Any, encryption_keys: Sequence[str] = (), **kwargs: Any) -> None:
        """Build the store, encrypting with the first key and decrypting with all of them.

        Args:
            *args: Passed to a2a-sdk's store.
            encryption_keys: Fernet keys, the encrypting key first. Empty
                stores configs unencrypted.
            **kwargs: Passed to a2a-sdk's store.
        """
        super().__init__(*args, **kwargs)
        if encryption_keys:
            from cryptography.fernet import Fernet, MultiFernet

            self._fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in encryption_keys])

    async def initialize(self) -> None:
        """Create the config table if it is missing, one server at a time.

        The lock is held on a connection of its own while a2a-sdk's setup runs
        on another, and released whether or not the setup succeeds.
        """
        if self._initialized:
            return
        async with self.engine.connect() as lock_conn:
            await lock_conn.execute(
                text("SELECT pg_advisory_lock(CAST(:key AS BIGINT))"),
                {"key": PUSH_CONFIG_SETUP_LOCK_KEY},
            )
            await lock_conn.commit()
            try:
                await super().initialize()
            finally:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(CAST(:key AS BIGINT))"),
                    {"key": PUSH_CONFIG_SETUP_LOCK_KEY},
                )
                await lock_conn.commit()
