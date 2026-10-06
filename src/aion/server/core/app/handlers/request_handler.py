"""Aion request handler extending the default A2A handler with preprocessors and Aion methods."""

import logging
from a2a.server.agent_execution import RequestContext
from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.cluster.task_store import LegacyTaskStoreAdapter
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.request_handlers.request_handler import validate, validate_request_params
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.task import apply_history_length
from aion.core.a2a import (
    ContextSummaryList,
    ContextView,
    DeleteContextParams,
    DeleteContextResult,
    GetContextParams,
    GetContextsParams,
)
from aion.core.runtime import ExtensionActivationError, aion_a2a_extension_registry
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from collections.abc import AsyncGenerator, Sequence
from google.protobuf import json_format
from types import SimpleNamespace
from typing import override
import uuid

from aion.server.agent.execution import AionActiveTaskRegistry
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.auth import has_individual_access
from aion.server.tasks.admission import ContextAdmission, InMemoryContextAdmission, holder_of
from aion.server.tasks.stores import BaseTaskStore
from aion.server.contexts import ContextService, ContextStateDeleter
from aion.server.tasks.contexts import ContextCatalog, InMemoryContextCatalog
from .request_preprocessors import A2ARequestPreprocessor, PreprocessingContext
from .terminal_task_projection import TerminalTaskProjection
from aion.server.a2a.response_extensions import ResponseServiceParameters

logger = logging.getLogger(__name__)


def _unversioned(versioned_store) -> BaseTaskStore:
    """The plain task store behind a2a-sdk's versioned one.

    a2a-sdk's handler reads and writes tasks through its versioned store: the
    store it was given, or ``LegacyTaskStoreAdapter`` around a plain one.
    Aion's own paths - admission, the closing Task of a stream, the context
    catalog - read tasks without a version, from the store underneath, which
    a versioned store names ``as_task_store`` as a2a-sdk's own does.
    """
    if isinstance(versioned_store, LegacyTaskStoreAdapter):
        return versioned_store.store
    return versioned_store.as_task_store


def _require_individual_access(context: ServerCallContext | None, task_id: str) -> None:
    """Refuse a caller without individual access as if ``task_id`` did not exist.

    Raises:
        TaskNotFoundError: The caller reaches no task by being its initiator.
    """
    if context is not None and not has_individual_access(context):
        raise TaskNotFoundError(f'Task {task_id} not found')


class AionRequestHandler(DefaultRequestHandlerV2):
    """Request handler implementation for Aion management operations."""

    def __init__(
            self,
            *args,
            preprocessors: list[A2ARequestPreprocessor] | None = None,
            admission: ContextAdmission | None = None,
            context_catalog: ContextCatalog | None = None,
            **kwargs,
    ) -> None:
        """Build the handler over a2a-sdk's own, with Aion's registry.

        ``task_store`` and ``event_stream`` are a2a-sdk's arguments and mean
        what they mean there: a ``VersionedTaskStore`` with an
        ``event_stream`` runs a2a-sdk's cluster mode, a plain store its
        single-process mode. ``StoreManager`` picks the pair.

        ``admission`` and ``context_catalog`` come from the same factory;
        omitted, contexts are reserved and catalogued in this process only,
        which is right for a handler over the in-memory store. The catalog
        must cover the same reservations admission writes, so a handler given
        only one of them is refused.
        """
        super().__init__(*args, **kwargs)
        self._active_task_registry = AionActiveTaskRegistry(
            agent_executor=self.agent_executor,
            task_store=self._versioned_store,
            push_sender=self._push_sender,
            event_stream=self._event_stream,
        )
        self._tasks = _unversioned(self._versioned_store)
        self._preprocessors = preprocessors or []
        if (admission is None) != (context_catalog is None):
            raise ValueError("admission and context_catalog are chosen together; pass both or neither")
        if admission is None:
            admission = InMemoryContextAdmission()
            context_catalog = InMemoryContextCatalog(self._tasks, admission)
        self._admission = admission
        self._contexts = ContextService(
            catalog=context_catalog,
            owner_resolver=self._tasks.owner_resolver,
            cancel_task=self._cancel_for_context_deletion,
            state_deleter=ContextStateDeleter(
                supported=lambda: self.agent_executor.agent.supports_context_state_deletion,
                delete=lambda *args, **kwargs: self.agent_executor.agent.delete_context_state(*args, **kwargs),
            ),
            forget_push_configs=self._forget_push_configs,
        )

    @override
    async def _setup_active_task(
            self,
            params: SendMessageRequest,
            call_context: ServerCallContext,
    ) -> tuple[ActiveTask, RequestContext]:
        """Admit, verify, then preprocess, then set up the active task.

        The order is the point. A preprocessor may have an external side
        effect - storing an attachment into the organization the request
        names - and metadata that merely parsed has not been accepted yet.
        Admitting the caller into the context and verifying first means a
        request the agent is about to reject can never trigger that side
        effect, nor write a push config or a task, and the preprocessor works
        from the verified projection instead of re-reading the raw request.
        Unary and streaming sends, and every extension handler behind them,
        come through here.
        """
        await self._admit(params, call_context)
        extensions = self.verify_declared_extensions(params, call_context)
        context = PreprocessingContext(
            extensions=extensions, call_context=call_context
        )

        await self._run_preprocessors(params, context)
        try:
            return await super()._setup_active_task(params, call_context)
        except Exception:
            await self._rollback_preprocessors(len(self._preprocessors))
            raise

    async def _admit(self, params: SendMessageRequest, call_context: ServerCallContext) -> None:
        """Admit the caller into the message's context, or refuse it as if the task did not exist.

        A continuation's context is its task's: the task has to be the
        caller's own, found through the owner-scoped store, and a ``contextId``
        sent with it has to match. A message without either starts a new
        context, whose ID is chosen here so it is reserved like any other.
        A caller without individual access continues no task.
        Every refusal is the same ``TaskNotFoundError``, whether another caller
        holds the context, it is being deleted, or the caller has no owner to
        hold it by: it says nothing about who holds what. An admitted caller
        with individual access is bound to the context, which is what makes it
        visible to that caller through the Context extension.

        Raises:
            TaskNotFoundError: The caller may not use the context or the task.
        """
        message = params.message
        if message.task_id:
            _require_individual_access(call_context, message.task_id)
            task = await self._tasks.get(message.task_id, call_context)
            if task is None or (message.context_id and message.context_id != task.context_id):
                raise TaskNotFoundError(f'Task {message.task_id} not found')
            message.context_id = task.context_id
        elif not message.context_id:
            message.context_id = str(uuid.uuid4())

        holder = holder_of(call_context, self._tasks.owner_resolver)
        member = self._contexts.member_of(call_context)
        if holder is None or not await self._admission.admit(message.context_id, holder, member):
            logger.info("Refused a message into a context its caller may not use")
            raise TaskNotFoundError(f'Task {message.task_id} not found' if message.task_id else 'Task not found')

    async def _run_preprocessors(
            self,
            params: SendMessageRequest,
            context: PreprocessingContext,
    ) -> None:
        """Run every preprocessor, compensating them all if one fails.

        The failing preprocessor is rolled back too, not just the ones before
        it: a preprocessor that stores a batch of files can reject the request
        with part of that batch already stored, and its own ``rollback`` is the
        only thing that knows about it.
        """
        for index, preprocessor in enumerate(self._preprocessors):
            try:
                await preprocessor.process(params, context)
            except Exception:
                await self._rollback_preprocessors(index + 1)
                raise

    async def _rollback_preprocessors(self, count: int) -> None:
        """Roll back the first ``count`` preprocessors, in reverse order.

        A failing rollback is logged rather than raised: it must not replace
        the error that caused the rollback, and the remaining preprocessors
        still need compensating.
        """
        for preprocessor in reversed(self._preprocessors[:count]):
            try:
                await preprocessor.rollback()
            except Exception:
                logger.exception(
                    "Preprocessor %s failed to roll back",
                    type(preprocessor).__name__,
                )

    def verify_declared_extensions(
            self,
            params: SendMessageRequest,
            call_context: ServerCallContext,
    ) -> AionRuntimeExtensions:
        """Reject invalid extension declarations from the request path.

        Runs the same collect/verify pipeline the executor's runtime-context
        builder repeats later, but before any task machinery exists: a client
        mistake (declaring an extension the agent hasn't enabled, missing
        co-activation, malformed payload) comes back as a plain
        InvalidParamsError response instead of surfacing as a producer-side
        "Execution failed" ERROR traceback in ActiveTask - and no idle
        ActiveTask is ever registered for the rejected request.

        Availability is a registry concern: an enabled extension whose
        runtime dependencies are missing on this deployment was marked via
        AionA2AExtensionRegistry.mark_unavailable() at startup, so the same
        pipeline rejects it here with the extension-authored reason - never
        silently handing the request to the primary flow the client
        explicitly asked to bypass.

        Returns:
            The verified extensions, which preprocessing acts on.

        Raises:
            InvalidParamsError: an extension declared active on the request
                fails verification for this agent.
        """
        declaration = SimpleNamespace(
            message=params.message,
            # Mirrors RequestContext.metadata: recurse into nested Structs so
            # collectors see the same plain-dict shape they see in execute().
            metadata=json_format.MessageToDict(params.metadata),
            requested_extensions=call_context.requested_extensions,
            # HeaderCollector reads its value from call_context.state["headers"]
            # (see descriptors.py); without it, every header-carried extension
            # - usage attribution among them - would fail verification here and
            # its payload would be missing from the projection preprocessing
            # relies on.
            call_context=call_context,
        )
        try:
            verified = AionRuntimeExtensions.collect(
                declaration, aion_a2a_extension_registry.get_all()
            )
        except ExtensionActivationError as ex:
            raise InvalidParamsError(message=str(ex)) from ex
        ResponseServiceParameters(verified.activated_uris).record(call_context)
        return verified

    @override
    async def on_get_task(self, params: GetTaskRequest, context: ServerCallContext) -> Task | None:
        """The caller's own task; a caller without individual access has none."""
        _require_individual_access(context, params.id)
        return await super().on_get_task(params, context)

    @override
    async def on_list_tasks(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        """The caller's own tasks; a caller without individual access lists none."""
        if not has_individual_access(context):
            return ListTasksResponse()
        return await super().on_list_tasks(params, context)

    @override
    async def on_create_task_push_notification_config(
            self,
            params: TaskPushNotificationConfig,
            context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Only a task's initiator directs its notifications."""
        _require_individual_access(context, params.task_id)
        return await super().on_create_task_push_notification_config(params, context)

    @override
    async def on_get_task_push_notification_config(
            self,
            params: GetTaskPushNotificationConfigRequest,
            context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Only a task's initiator reads its notification configs."""
        _require_individual_access(context, params.task_id)
        return await super().on_get_task_push_notification_config(params, context)

    @override
    async def on_list_task_push_notification_configs(
            self,
            params: ListTaskPushNotificationConfigsRequest,
            context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Only a task's initiator lists its notification configs."""
        _require_individual_access(context, params.task_id)
        return await super().on_list_task_push_notification_configs(params, context)

    @override
    async def on_delete_task_push_notification_config(
            self,
            params: DeleteTaskPushNotificationConfigRequest,
            context: ServerCallContext,
    ) -> None:
        """Only a task's initiator removes its notification configs."""
        _require_individual_access(context, params.task_id)
        await super().on_delete_task_push_notification_config(params, context)

    async def on_get_contexts(
            self,
            params: GetContextsParams,
            context: ServerCallContext | None = None,
    ) -> ContextSummaryList:
        """``GetContexts``: the caller's contexts, most recently active first."""
        return await self._contexts.get_contexts(params, context)

    async def on_get_context(
            self,
            params: GetContextParams,
            context: ServerCallContext | None = None,
    ) -> ContextView:
        """``GetContext``: one context the caller is bound to.

        Raises:
            ContextNotFound: The caller cannot see the context.
        """
        return await self._contexts.get_context(params, context)

    async def on_delete_context(
            self,
            params: DeleteContextParams,
            context: ServerCallContext | None = None,
    ) -> DeleteContextResult:
        """``DeleteContext``: remove the caller's access, deleting the context with its last caller.

        Raises:
            ContextDeletionInProgress: The deletion is durable but not finished.
            ContextNotDeletable: The deletion was refused definitively.
        """
        return await self._contexts.delete_context(params, context)

    async def resume_pending_context_deletions(self) -> None:
        """Drive every context deletion left unfinished, as the server starts."""
        await self._contexts.resume_pending_deletions()

    async def _cancel_for_context_deletion(self, task_id: str) -> Task | None:
        """Cancel one task of a context being deleted, whoever owns it.

        The same two branches as ``on_cancel_task``, without its caller
        check: the deletion is entitled to every task of the context. A task
        executing here is cancelled as its own owner; any other gets
        a2a-sdk's remote cancellation - ``CANCELED`` written over its stored
        version - through the store with unscoped access.

        Returns:
            The cancelled task, or ``None`` when no such task exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        local = await self._active_task_registry.cancel_local_as_owner(task_id)
        if local is not None:
            return local
        stored = await self._versioned_store.get(task_id, None)
        if stored is None:
            return None
        if stored.task.status.state in TERMINAL_TASK_STATES:
            raise TaskNotCancelableError
        return await self._cancel_remote(task_id, stored.task, stored.version, None)

    async def _forget_push_configs(self, task_ids: Sequence[str]) -> None:
        """Remove every owner's push configurations of removed tasks from an in-memory config store.

        The durable config store is cleared in the deletion's own storage
        transaction; the in-memory one keeps configs per owner and has no
        owner-agnostic delete, so its entries are removed here.
        """
        store = self._push_config_store
        if not isinstance(store, InMemoryPushNotificationConfigStore):
            return
        removed = set(task_ids)
        with store.lock:
            for owner_infos in store._push_notification_infos.values():  # noqa: SLF001
                for task_id in removed & owner_infos.keys():
                    del owner_infos[task_id]

    @override
    async def on_message_send(
            self,
            params: SendMessageRequest,
            context: ServerCallContext,
    ) -> Task:
        """Unary handler that always answers with a Task.

        The base handler answers with a Message when the agent never created a
        task (message-only interaction). Aion's contract is the same on both
        the unary and the streaming path — the caller receives a Task — so such
        a reply is resolved against the store.
        """
        result = await super().on_message_send(params, context)
        if isinstance(result, Task):
            return result

        task_id = result.task_id or params.message.task_id
        # The store is read directly: ActiveTask.get_task() waits on a flag that
        # a message-only interaction never sets, which would hang the request.
        task = await self._tasks.get(task_id, context) if task_id else None

        if task is None:
            # Aion's executor always announces a Task, so a message with no task
            # behind it means the executor is out of contract. Fail loudly
            # rather than answer with a task id that resolves to nothing.
            logger.error(
                "message/send answered with a Message and no stored task (task_id=%s)",
                task_id or "<none>",
            )
            raise InternalError(
                message="Agent answered with a Message instead of a Task."
            )

        return apply_history_length(task, params.configuration)

    async def on_message_send_stream(
            self,
            params: SendMessageRequest,
            context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        """Stream handler that closes the stream with the final Task.

        Overrides DefaultRequestHandler to emit the final Task object after
        all other events have been streamed. This ensures the client receives
        a complete Task snapshot with all accumulated state at stream end.

        How much detail a run's own events carry is not decided here: it is a
        property of the request an extension parses (the evolution directive's
        `view`, say), so the producer shapes and drops its events before they
        reach this handler. This layer knows nothing about any extension's
        payload.

        Status updates carrying a non-active state are withheld and replaced by
        a complete Task snapshot, so the client observes task completion through
        exactly one event. See TerminalTaskProjection.
        """
        projection = TerminalTaskProjection(
            task_store=self._tasks,
            call_context=context,
            task_transform=lambda task: apply_history_length(task, params.configuration),
        )
        async for event in projection.project(super().on_message_send_stream(params, context)):
            yield event

    @override
    async def on_cancel_task(
            self,
            params: CancelTaskRequest,
            context: ServerCallContext,
    ) -> Task:
        """Cancel a task; only its initiator may.

        A caller without individual access cancels nothing: the task answers
        as if it did not exist. The rest is a2a-sdk's: a task this instance
        holds is cancelled through its executor, any other gets ``CANCELED``
        written over its stored version, and the instance executing it stops
        at its next write.
        """
        _require_individual_access(context, params.id)
        return await super().on_cancel_task(params, context)

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(
            self,
            params: SubscribeToTaskRequest,
            context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        """a2a-sdk's resubscription, closed by the stored Task.

        a2a-sdk serves it: from the ``ActiveTask`` when this instance holds
        the task, otherwise from a snapshot followed by the task's journal,
        until the task stops. ``TerminalTaskProjection`` closes either stream
        with the stored Task, as it closes every stream this handler opens:
        the journal's last event may be a status update, and a client of this
        server always sees a task stop through a Task.

        A task that already has an outcome is refused with
        ``UnsupportedOperationError``, as a2a-sdk refuses it. The check runs
        before the projection, because the projection would otherwise answer
        the refusal with the stored Task.
        """
        _require_individual_access(context, params.id)
        stored = await self._versioned_store.get(params.id, context)
        if stored is None:
            raise TaskNotFoundError
        if stored.task.status.state in TERMINAL_TASK_STATES:
            raise UnsupportedOperationError(
                message=f'Task {params.id} is in terminal state: '
                f'{TaskState.Name(stored.task.status.state)}'
            )

        projection = TerminalTaskProjection(
            task_store=self._tasks,
            call_context=context,
            task_id=params.id,
        )
        async for event in projection.project(super().on_subscribe_to_task(params, context)):
            yield event
