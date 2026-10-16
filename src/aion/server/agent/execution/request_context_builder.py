"""Request context builder, and the query that finds the task a message without a task id continues."""

from a2a.server.agent_execution import RequestContext, RequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.id_generator import IDGenerator
from a2a.types.a2a_pb2 import SendMessageRequest, Task

from aion.server.auth import has_individual_access
from aion.server.tasks.stores.base_task_store import BaseTaskStore
from aion.server.a2a.utils import is_task_interrupted


class AionRequestContextBuilder(RequestContextBuilder):
    """Builds the request context, and finds the waiting task a message continues.

    Which task a message without a task id continues is decided once, before
    the task is set up: ``AionRequestHandler`` asks ``interrupted_task`` and
    makes the message name the task it continues. ``build`` then takes the
    message's task id as it is.
    """

    def __init__(
            self,
            task_store: BaseTaskStore | None = None,
            task_id_generator: IDGenerator | None = None,
            context_id_generator: IDGenerator | None = None,
            auto_discover_interrupted_task: bool = True,
    ) -> None:
        """Initializes the builder.

        Args:
            task_store: The task store ``interrupted_task`` searches.
            task_id_generator: ID generator for new task IDs. Defaults to None.
            context_id_generator: ID generator for new context IDs. Defaults to None.
            auto_discover_interrupted_task: If True, a message with a context
                id and no task id continues the caller's task waiting for
                input in that context (``interrupted_task``).
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
        """Builds the request context for an agent execution, without referred tasks.

        Args:
            context: The server call context, containing metadata about the call.
            params: The parameters of the incoming message send request.
            task_id: The ID of the task being executed.
            context_id: The ID of the current execution context.
            task: The primary task object associated with the request.

        Returns:
            An instance of RequestContext populated with the provided information.
        """
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

    async def interrupted_task(
            self,
            context_id: str,
            context: ServerCallContext,
            routed_extension: str | None = None,
    ) -> Task | None:
        """The caller-owned waiting task of one handler that a message to ``context_id`` without a task id continues.

        It is the caller's newest task in the context of the handler the
        message addresses - the toolkit whose extension ``routed_extension``
        names, or the agent's framework adapter for None - when that task
        waits for input (``INPUT_REQUIRED``, ``AUTH_REQUIRED``). A waiting
        task of another handler is not continued by the message.

        None when auto-discovery is off or there is no such task. A caller
        without individual access owns none to return to: its shared
        attribution cannot tell whose interrupted task it would be.
        """
        if (
            not self._auto_discover_interrupted_task
            or not self._task_store
            or not has_individual_access(context)
        ):
            return None

        last_task = await self._task_store.get_context_last_routed_task(
            context_id=context_id,
            routed_extension=routed_extension,
            context=context,
        )
        if last_task is None:
            return None

        if not is_task_interrupted(last_task):
            return None
        return last_task
