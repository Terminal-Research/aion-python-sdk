"""The PostgreSQL push notification config store, with several encryption keys."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from a2a.server.tasks.database_push_notification_config_store import (
    DatabasePushNotificationConfigStore,
)

__all__ = ["AionDatabasePushNotificationConfigStore"]


class AionDatabasePushNotificationConfigStore(DatabasePushNotificationConfigStore):
    """a2a-sdk's database config store with several encryption keys.

    a2a-sdk takes a single Fernet key. This store takes the deployment's list
    of keys and gives a2a-sdk a ``MultiFernet`` in its place: the first key
    encrypts every config written, and a config written under any key in the
    list is still read. a2a-sdk touches the key only through its
    ``encrypt``/``decrypt`` calls, which ``MultiFernet`` provides with the
    same signatures and the same ``InvalidToken`` on failure.

    A config encrypted under a key no longer in the list cannot be read.
    a2a-sdk logs it and leaves it out of what it returns, so delivery goes on
    to the task's other configs.

    The table is the SDK migrations' (revision ``009``), so the store never
    creates it: a2a-sdk's ``create_table`` is off.
    """

    def __init__(self, *args: Any, encryption_keys: Sequence[str] = (), **kwargs: Any) -> None:
        """Build the store, encrypting with the first key and decrypting with all of them.

        Args:
            *args: Passed to a2a-sdk's store.
            encryption_keys: Fernet keys, the encrypting key first. Empty
                stores configs unencrypted.
            **kwargs: Passed to a2a-sdk's store.
        """
        kwargs.setdefault("create_table", False)
        super().__init__(*args, **kwargs)
        if encryption_keys:
            from cryptography.fernet import Fernet, MultiFernet

            self._fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in encryption_keys])
