"""Abstract base class for A2A task persistence backends."""

from abc import abstractmethod
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import Task



class BaseTaskStore(TaskStore):
    """
   Abstract base class for task storage implementations.

   Extends TaskStore with methods for retrieving context IDs and tasks
   associated with specific contexts, with optional pagination support.
   """

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
    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[str]:
        """
       Retrieve a list of context IDs with optional pagination.

       Args:
           offset: Number of records to skip (for pagination)
           limit: Maximum number of records to return

       Returns:
           List of context ID strings
       """
        pass

    @abstractmethod
    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[Task]:
        """
        Retrieve tasks associated with a specific context.

        Args:
            context_id: The context identifier to filter tasks by
            offset: Number of records to skip (for pagination)
            limit: Maximum number of records to return

        Returns:
            List of Task objects belonging to the specified context
        """
        pass

    @abstractmethod
    async def get_active_tasks(self) -> List[Task]:
        """
        Retrieve every task the store still presents as running.

        Deliberately owner-agnostic and unpaginated: this serves process-level
        maintenance, which asks about the store as a whole rather than on
        behalf of a caller. Its one consumer today is the startup reap of tasks
        an interrupted process left active.

        Returns:
            All tasks in an active state, in no particular order
        """
        pass

    @abstractmethod
    async def get_context_last_task(self, context_id: str) -> Optional[Task]:
        """
        Retrieve the most recent task for a specific context.

        Args:
            context_id: The context identifier to get the last task for

        Returns:
            The most recent Task object for the context, or None if no tasks exist
        """
        pass
