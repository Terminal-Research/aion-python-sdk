"""Aion request handler extending the default A2A handler with preprocessors and Aion methods."""

import logging
from a2a.server.agent_execution import RequestContext
from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.cluster.version import TaskVersion
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
)
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotFoundError,
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
from aion.db.postgres.events import TaskEventKind
from aion.server.auth import has_individual_access
from aion.server.tasks.admission import ContextAdmission, InMemoryContextAdmission, holder_of
from aion.server.tasks.notifications import TaskEventListener
from aion.server.tasks.ownership import OwnershipProvider
from aion.server.tasks.ownership.config import CANCEL_WAIT_SECONDS
from aion.server.contexts import ContextService, ContextStateDeleter
from aion.server.tasks.contexts import ContextCatalog, InMemoryContextCatalog
from .request_preprocessors import A2ARequestPreprocessor, PreprocessingContext
from .terminal_task_projection import TerminalTaskProjection
from aion.server.a2a.response_extensions import ResponseServiceParameters

logger = logging.getLogger(__name__)


async def _no_events() -> AsyncGenerator[Event]:
    """An empty event stream, for a subscription with nothing to wait for.

    Handed to ``TerminalTaskProjection``, it produces exactly the stored Task:
    the projection closes every stream that way, so a settled task needs no
    separate reply path of its own.
    """
    return
    yield  # pragma: no cover - unreachable, and what makes this a generator


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
            ownership_provider: OwnershipProvider | None = None,
            event_listener: TaskEventListener | None = None,
            admission: ContextAdmission | None = None,
            context_catalog: ContextCatalog | None = None,
            **kwargs,
    ) -> None:
        """Build the handler and hand the registry its ownership provider.

        The provider arrives from the same factory that chose the task store,
        keeping the pair one decision. Omitted, the registry falls back to the
        single-process provider, which is what a handler built without a
        durable store should get.

        ``event_listener`` is threaded in the same way rather than read off
        the module-level ``store_manager`` singleton at call time: a handler
        built directly, outside ``AppFactory`` - as every ownership test does
        - would otherwise reach a singleton nothing ever initialized. ``None``
        is a legitimate value, not just a not-yet-wired default: it is what an
        in-memory-backed handler should have, since ``on_cancel_task``'s
        second branch is unreachable without a durable store to hold the
        claim it waits on.

        ``admission`` and ``context_catalog`` come from the same factory too;
        omitted, contexts are reserved and catalogued in this process only,
        which is right for a handler over the in-memory store. The catalog
        must cover the same reservations admission writes, so a handler given
        only one of them is refused.
        """
        super().__init__(*args, **kwargs)
        self._active_task_registry = AionActiveTaskRegistry(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            push_sender=self._push_sender,
            ownership_provider=ownership_provider,
        )
        self._event_listener = event_listener
        self._preprocessors = preprocessors or []
        if (admission is None) != (context_catalog is None):
            raise ValueError("admission and context_catalog are chosen together; pass both or neither")
        if admission is None:
            admission = InMemoryContextAdmission()
            context_catalog = InMemoryContextCatalog(self.task_store, admission)
        self._admission = admission
        self._contexts = ContextService(
            catalog=context_catalog,
            owner_resolver=self.task_store.owner_resolver,
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
            task = await self.task_store.get(message.task_id, call_context)
            if task is None or (message.context_id and message.context_id != task.context_id):
                raise TaskNotFoundError(f'Task {message.task_id} not found')
            message.context_id = task.context_id
        elif not message.context_id:
            message.context_id = str(uuid.uuid4())

        holder = holder_of(call_context, self.task_store.owner_resolver)
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

        The same three branches as ``on_cancel_task``, without its caller
        check: the deletion is entitled to every task of the context. A task
        executing here is cancelled as its own owner; any other is reached
        through the store with unscoped access.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        local = await self._active_task_registry.cancel_local_as_owner(task_id)
        if local is not None:
            return local
        return await self._cancel_elsewhere(task_id, None)

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
        task = await self.task_store.get(task_id, context) if task_id else None

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
            task_store=self.task_store,
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
        """Cancel a task, wherever in the process fleet it is actually executing.

        Only its initiator may, so a caller without individual access cancels
        nothing: the task answers as if it did not exist.

        The rest is a2a-sdk's: it reads the task through the owner-scoped
        store, refuses a task that is missing or already has an outcome, and
        then cancels it here (``_cancel_local``) when this process holds its
        execution, or elsewhere (``_cancel_remote``) when it does not. Both
        hooks are Aion's, because a task here is owned by a claim rather than
        by a version the base compares - see each of them.
        """
        _require_individual_access(context, params.id)
        return await super().on_cancel_task(params, context)

    @override
    async def _cancel_local(self, task_id: str, context: ServerCallContext) -> Task:
        """Cancel the execution this process is running, through the executor.

        ``AionActiveTaskRegistry.cancel_local`` cancels through the ordinary
        A2A executor path - rescue included, for evolution runs - and the
        resulting terminal task is returned with no further waiting. An
        execution that ended between the base handler's check and this call
        is no longer here to cancel, so the claim path decides instead.
        """
        local = await self._active_task_registry.cancel_local(task_id, context)
        if local is not None:
            return local
        return await self._cancel_or_not_found(task_id, context)

    @override
    async def _cancel_remote(
            self,
            task_id: str,
            task: Task,
            version: TaskVersion,
            context: ServerCallContext,
    ) -> Task:
        """Cancel a task this process is not executing, through its claim.

        The base writes ``CANCELED`` over a version; here the execution's
        owner is the process holding its claim, and only that process can run
        its teardown. ``task`` and ``version`` are the base handler's read and
        are not used: ``_cancel_elsewhere`` decides from the claim.
        """
        return await self._cancel_or_not_found(task_id, context)

    async def _cancel_or_not_found(self, task_id: str, context: ServerCallContext) -> Task:
        task = await self._cancel_elsewhere(task_id, context)
        if task is None:
            raise TaskNotFoundError
        return task

    async def _cancel_elsewhere(
            self,
            task_id: str,
            context: ServerCallContext | None,
    ) -> Task | None:
        """Cancel a task through its claim: one held elsewhere, or none at all.

        Another process holding a live claim cannot have its teardown run from
        here, so the claim is marked (``BaseTaskStore.request_cancellation``)
        and the owner's own cancellation - discovered over
        ``TASK_EVENT_CHANNEL`` - is awaited, bounded. A wait that outruns the
        bound answers with the task as currently stored; the terminal state
        still reaches the client later, over push or ``tasks/resubscribe``.
        With no live claim anywhere there is no owner to ask, and the terminal
        state is written directly
        (``BaseTaskStore.cancel_with_ownership_revocation``).

        ``context`` scopes the store to the caller's own tasks; ``None``
        reaches every owner's, for a caller entitled to all of them.

        Returns:
            The task as cancelled - or as stored when a remote owner's
            cancellation outran the wait - or ``None`` when no such task
            exists.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        # Registered before request_cancellation's transaction commits, not
        # after: the owner's own CANCEL_RESOLVED notification can only follow
        # that commit, but nothing stops it from arriving almost immediately -
        # registering any later would race that notification and could miss
        # it. See TaskEventListener.register / Waiter.
        waiter = (
            self._event_listener.register(TaskEventKind.CANCEL_RESOLVED, task_id)
            if self._event_listener
            else None
        )
        try:
            marked = await self.task_store.request_cancellation(task_id, context)
            if marked is None:
                return None

            if marked:
                if waiter is not None:
                    await waiter.wait(timeout=CANCEL_WAIT_SECONDS)
                # Read regardless of whether the wait was woken or timed out:
                # a woken wait still needs the settled row, and a timed-out
                # one may have missed a notification lost to a listener
                # reconnect rather than a cancellation that never happened.
                return await self.task_store.get(task_id, context)
        finally:
            if waiter is not None:
                waiter.release()

        return await self.task_store.cancel_with_ownership_revocation(
            task_id,
            context,
        )

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
        """Resubscribe handler applying the same terminal-Task projection.

        A task that already has an outcome has no execution to join, and the
        SDK will not start one for it. Its stream is empty and the projection
        closes it with the stored Task - which is the answer the client asked
        for, and the reason the reaper settles a task rather than deleting it.
        a2a-sdk's own handler refuses such a task with
        ``UnsupportedOperationError`` instead.

        The base handler is not delegated to for the live case either: it
        attaches through ``get_or_create``, which would start an empty
        execution here for a task another process is running.
        ``AionActiveTaskRegistry.get_for_attach`` attaches to an execution
        this process holds, refuses a task another process is running with
        ``TaskOwnershipBusy``, and otherwise leaves the stream empty.
        """
        _require_individual_access(context, params.id)
        active_task = await self._active_task_registry.get_for_attach(
            params.id,
            context,
        )
        projection = TerminalTaskProjection(
            task_store=self.task_store,
            call_context=context,
            task_id=params.id,
        )
        source = (
            active_task.subscribe(include_initial_task=True)
            if active_task is not None
            else _no_events()
        )

        async for event in projection.project(source):
            yield event
