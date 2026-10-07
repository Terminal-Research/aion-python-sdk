"""The PostgreSQL push notification config store, with a table setup servers can share."""

from __future__ import annotations

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
    """a2a-sdk's database config store whose table setup takes the setup lock.

    The server calls :meth:`initialize` while it starts, so the table exists
    before the first request reaches the store rather than being created by it.
    """

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
