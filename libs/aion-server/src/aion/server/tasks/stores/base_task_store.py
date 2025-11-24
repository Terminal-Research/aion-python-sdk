from abc import abstractmethod
from typing import Optional, List

from a2a.server.tasks import TaskStore
from a2a.types import Task



class BaseTaskStore(TaskStore):
    """
   Abstract base class for task storage implementations.

   Extends TaskStore with methods for retrieving context IDs and tasks
   associated with specific contexts, with optional pagination support.
   """

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
    async def get_context_last_task(self, context_id: str) -> Optional[Task]:
        """
        Retrieve the most recent task for a specific context.

        Args:
            context_id: The context identifier to get the last task for

        Returns:
            The most recent Task object for the context, or None if no tasks exist
        """
        pass
