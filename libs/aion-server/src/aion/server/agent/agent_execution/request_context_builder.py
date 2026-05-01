from a2a.server.agent_execution import RequestContext, RequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.id_generator import IDGenerator
from a2a.types.a2a_pb2 import SendMessageRequest, Task

from aion.server.tasks.stores.base_task_store import BaseTaskStore
from aion.server.utils import check_if_task_is_interrupted


class AionRequestContextBuilder(RequestContextBuilder):
    """Builds request context and populates referred tasks."""

    def __init__(
            self,
            task_store: BaseTaskStore | None = None,
            task_id_generator: IDGenerator | None = None,
            context_id_generator: IDGenerator | None = None,
            auto_discover_interrupted_task: bool = True,
    ) -> None:
        """Initializes the SimpleRequestContextBuilder.

        Args:
            task_store: The TaskStore instance to use for fetching referred tasks.
                Required if `should_populate_referred_tasks` is True.
            task_id_generator: ID generator for new task IDs. Defaults to None.
            context_id_generator: ID generator for new context IDs. Defaults to None.
            auto_discover_interrupted_task: If True and no task_id is provided,
                attempts to find the last interrupted task for the given context_id.
        """
        self._task_store = task_store
        self._task_id_generator = task_id_generator
        self._context_id_generator = context_id_generator
        self._auto_discover_interrupted_task = auto_discover_interrupted_task

    async def build(
            self,
            context: ServerCallContext,
            params: SendMessageRequest | None = None,
            task_id: str | None = None,
            context_id: str | None = None,
            task: Task | None = None,
    ) -> RequestContext:
        """Builds the request context for an agent execution.

        Args:
            context: The server call context, containing metadata about the call.
            params: The parameters of the incoming message send request.
            task_id: The ID of the task being executed.
            context_id: The ID of the current execution context.
            task: The primary task object associated with the request.

        Returns:
            An instance of RequestContext populated with the provided information
            and potentially a list of related tasks.
        """
        if not task_id and self._auto_discover_interrupted_task and context_id:
            discovered = await self._find_interrupted_task(context_id)
            if discovered:
                task_id = discovered.id
                task = discovered

        return RequestContext(
            call_context=context,
            request=params,
            task_id=task_id,
            context_id=context_id,
            task=task,
            related_tasks=[],
            task_id_generator=self._task_id_generator,
            context_id_generator=self._context_id_generator,
        )

    async def _find_interrupted_task(self, context_id: str) -> Task | None:
        """Returns the last interrupted task for the given context, or None."""
        if not self._task_store:
            return None

        last_task = await self._task_store.get_context_last_task(context_id=context_id)
        if not check_if_task_is_interrupted(last_task):
            return None

        return last_task
