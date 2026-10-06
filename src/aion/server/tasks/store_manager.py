"""Module-level task store manager that holds the active store instance for the server process."""

import logging
from typing import Optional

from a2a.server.cluster import DatabaseTaskEventStream, TaskEventStream, VersionedTaskStore
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope

from aion.db.postgres import db_manager
from .admission import ContextAdmission, InMemoryContextAdmission, PostgresContextAdmission
from .contexts import ContextCatalog, InMemoryContextCatalog, PostgresContextCatalog
from .stores import (
    BaseTaskStore,
    InMemoryTaskStore,
    PostgresTaskStore,
    PostgresVersionedTaskStore,
)

logger = logging.getLogger(__name__)


class StoreManager:
    """
    Singleton manager for task store implementations.

    Automatically selects the appropriate storage backend based on database
    manager initialization state.

    With PostgreSQL the server runs in a2a-sdk's cluster mode: the request
    handler gets a ``VersionedTaskStore`` and a ``TaskEventStream``, so any
    instance serving the agent over the same database can execute, follow
    and cancel any task. Without a database the store is in memory, has no
    versions and no stream, and the server is a single instance - a2a-sdk's
    single-process mode.
    """

    def __init__(self):
        self._is_initialized = False
        self._store: Optional[InMemoryTaskStore | PostgresTaskStore] = None
        self._versioned_store: Optional[VersionedTaskStore] = None
        self._event_stream: Optional[TaskEventStream] = None
        self._admission: Optional[ContextAdmission] = None
        self._context_catalog: Optional[ContextCatalog] = None

    def initialize(
            self,
            agent_id: str,
            *,
            guard_inline_files: bool = False,
            owner_resolver: OwnerResolver = resolve_user_scope,
    ):
        """
        Initialize the store manager with appropriate storage backend.

        Selects PostgresTaskStore if database manager is initialized,
        otherwise uses InMemoryTaskStore as fallback. Safe to call multiple times.

        Args:
            agent_id: Identity of the agent this process serves. Scopes every
                task the Postgres backend touches, so several agents can
                share one database. Unused by the in-memory fallback, which
                is already isolated by being unshared.
            guard_inline_files: True when a file storage backend is
                installed, so the store strips inline file content that
                reaches it unconverted. Passed by the component that
                installs the backend; the store never reads settings for it.
            owner_resolver: Resolves a call's owner from its
                ``ServerCallContext``. ``AppFactory`` passes the agent's own
                resolver, so the tasks and the agent's framework state name
                the same owner. Defaults to a2a-sdk's ``resolve_user_scope``.
        """
        if self._is_initialized:
            logger.warning("Tried to initialize store, already initialized")
            return

        if db_manager.is_initialized:
            task_store = PostgresTaskStore(
                agent_id=agent_id,
                owner_resolver=owner_resolver,
                guard_inline_files=guard_inline_files,
            )
            versioned_store = PostgresVersionedTaskStore(task_store)
            # The tables come from the SDK's own migrations, like every other
            # table here; the stream must not create them on first use.
            event_stream = DatabaseTaskEventStream(db_manager.get_engine(), create_table=False)
            admission = PostgresContextAdmission(agent_id, db_manager)
            context_catalog = PostgresContextCatalog(agent_id, db_manager)
        else:
            task_store = InMemoryTaskStore(owner_resolver, guard_inline_files=guard_inline_files)
            versioned_store = None
            event_stream = None
            admission = InMemoryContextAdmission()
            context_catalog = InMemoryContextCatalog(task_store, admission)

        self._is_initialized = True
        self._store = task_store
        self._versioned_store = versioned_store
        self._event_stream = event_stream
        self._admission = admission
        self._context_catalog = context_catalog

    def get_store(self) -> BaseTaskStore:
        """
        Get the initialized task store instance.

        Returns:
           BaseTaskStore: The active task store implementation

        Raises:
           RuntimeError: If called before initialization
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get a store without initialization")
        return self._store

    def get_handler_store(self) -> BaseTaskStore | VersionedTaskStore:
        """Return the store the request handler is built over.

        The versioned store over :meth:`get_store` with PostgreSQL, so a2a-sdk
        runs in cluster mode; the plain store otherwise, which a2a-sdk wraps
        in ``LegacyTaskStoreAdapter`` itself.

        Raises:
            RuntimeError: If called before initialization.
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get the handler store without initialization")
        return self._versioned_store if self._versioned_store is not None else self._store

    def get_event_stream(self) -> Optional[TaskEventStream]:
        """Return the cross-instance event stream, if this store has one.

        ``None`` for the in-memory backend, which serves one instance only.

        Raises:
            RuntimeError: If called before initialization.
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get the event stream without initialization")
        return self._event_stream

    def get_admission(self) -> ContextAdmission:
        """Return the context reservations kept beside the active task store.

        Chosen with the store, as framework state is: PostgreSQL reservations
        for a PostgreSQL store, this process's for the in-memory one.

        Raises:
            RuntimeError: If called before initialization.
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get context admission without initialization")
        return self._admission

    def get_context_catalog(self) -> ContextCatalog:
        """Return the context catalog over the active task store and reservations.

        Raises:
            RuntimeError: If called before initialization.
        """
        if not self._is_initialized:
            raise RuntimeError("Trying to get the context catalog without initialization")
        return self._context_catalog


store_manager = StoreManager()
