"""Abstract base class for A2A task persistence backends."""

from abc import abstractmethod
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import Task

from aion.server.files.a2a import strip_inline_file_content


class BaseTaskStore(TaskStore):
    """
   Abstract base class for task storage implementations.

   Extends TaskStore with methods for retrieving context IDs and tasks
   associated with specific contexts, with optional pagination support.

   Args:
       guard_inline_files: True when a storage backend converts inline file
           content before it is persisted. The store then strips any inline
           file content that reaches it anyway, since with a backend
           installed that is a bug in the emitting path rather than data to
           keep. Set by whoever installs the backend - see
           ``StoreManager.initialize`` - so the guard cannot disagree with
           what is actually running.
   """

    def __init__(self, *, guard_inline_files: bool = False) -> None:
        super().__init__()
        self._guard_inline_files = guard_inline_files

    @property
    def guards_inline_files(self) -> bool:
        """Whether this store strips inline file content before persisting."""
        return self._guard_inline_files

    def _persistable(self, task: Task) -> Task:
        """Return the task as it may be written: guarded when so configured."""
        if not self._guard_inline_files:
            return task
        return strip_inline_file_content(task)

    @abstractmethod
    async def cancel_with_ownership_revocation(
            self,
            task_id: str,
            context: Optional[ServerCallContext] = None,
    ) -> Optional[Task]:
        """Cancel a task in storage, without an ``ActiveTask`` being involved.

        Cancellation is a control-plane operation: the process that receives it
        need not be the one executing the task, and building a local runtime
        for someone else's task in order to cancel it is precisely what must
        not happen. A durable store also drops the execution lease here, which
        is how the actual owner learns it is no longer one.

        The already-terminal case is reported as an error rather than through
        the returned state: a successful cancellation is itself terminal, so
        afterwards the two are indistinguishable.

        Args:
            task_id: Identifier of the task to cancel.
            context: Server call context, when one exists.

        Returns:
            The canceled task, or ``None`` when no such task exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        pass

    @abstractmethod
    async def request_cancellation(
            self,
            task_id: str,
            context: Optional[ServerCallContext] = None,
    ) -> Optional[bool]:
        """Ask this task's owner to cancel it, without writing a terminal state.

        The non-owner mirror of :meth:`cancel_with_ownership_revocation`:
        used when there may be a live execution elsewhere that can give the
        task a graceful, reported cancellation if only it is asked - see
        ``AionRequestHandler.on_cancel_task`` for the three-way branch this
        feeds into, and ``PostgresTaskStore.request_cancellation`` for the
        durable implementation's fencing.

        Args:
            task_id: Identifier of the task to cancel.
            context: Server call context, when one exists.

        Returns:
            ``True`` when a live claim was marked and the caller should wait
            for the terminal write it triggers. ``False`` when the task
            exists but nothing holds a claim on it, meaning the caller must
            fall back to :meth:`cancel_with_ownership_revocation`. ``None``
            when no such task exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        pass

    @abstractmethod
    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: Optional[ServerCallContext] = None,
    ) -> List[str]:
        """
       Retrieve a list of context IDs with optional pagination.

       Args:
           offset: Number of records to skip (for pagination)
           limit: Maximum number of records to return
           context: Server call context used to resolve the exact owner

       Returns:
           List of context ID strings
       """
        pass

    @abstractmethod
    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: Optional[ServerCallContext] = None,
    ) -> List[Task]:
        """
        Retrieve tasks associated with a specific context.

        Args:
            context_id: The context identifier to filter tasks by
            offset: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            context: Server call context used to resolve the exact owner

        Returns:
            List of Task objects belonging to the specified context
        """
        pass

    @abstractmethod
    async def get_context_last_task(
            self,
            context_id: str,
            context: Optional[ServerCallContext] = None,
    ) -> Optional[Task]:
        """
        Retrieve the most recent task for a specific context.

        Args:
            context_id: The context identifier to get the last task for
            context: Server call context used to resolve the exact owner

        Returns:
            The most recent Task object for the context, or None if no tasks exist
        """
        pass
