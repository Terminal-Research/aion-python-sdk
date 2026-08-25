"""Aion request handler extending the default A2A handler with preprocessors and Aion methods."""

import logging
from a2a.server.agent_execution import RequestContext
from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.request_handlers.request_handler import validate, validate_request_params
from a2a.types import (
    CancelTaskRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
)
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotFoundError,
)
from a2a.utils.task import apply_history_length
from aion.core.a2a import ContextsList, Conversation, GetContextParams, GetContextsListParams
from aion.core.runtime import ExtensionActivationError, aion_a2a_extension_registry
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from collections.abc import AsyncGenerator
from functools import wraps
from google.protobuf import json_format
from types import SimpleNamespace
from typing import override

from aion.server.agent.execution import AionActiveTaskRegistry
from aion.server.tasks import store_manager
from aion.db.postgres.events import TaskEventKind
from aion.server.tasks.notifications import TaskEventListener
from aion.server.tasks.ownership import OwnershipProvider
from aion.server.tasks.ownership.config import CANCEL_WAIT_SECONDS
from aion.server.a2a.conversation import ConversationBuilder
from .request_preprocessors import A2ARequestPreprocessor
from .terminal_task_projection import TerminalTaskProjection

logger = logging.getLogger(__name__)


def _with_preprocessors(method):
    """Decorator that runs all registered preprocessors before a handler method.

    On success the wrapped method executes normally. On any exception,
    rolls back preprocessors in reverse order before re-raising.
    """
    @wraps(method)
    async def wrapper(self, params, *args, **kwargs):
        for preprocessor in self._preprocessors:
            await preprocessor.process(params)
        try:
            return await method(self, params, *args, **kwargs)
        except Exception:
            for preprocessor in reversed(self._preprocessors):
                await preprocessor.rollback()
            raise
    return wrapper


async def _no_events() -> AsyncGenerator[Event]:
    """An empty event stream, for a subscription with nothing to wait for.

    Handed to ``TerminalTaskProjection``, it produces exactly the stored Task:
    the projection closes every stream that way, so a settled task needs no
    separate reply path of its own.
    """
    return
    yield  # pragma: no cover - unreachable, and what makes this a generator


class AionRequestHandler(DefaultRequestHandlerV2):
    """Request handler implementation for Aion management operations."""

    def __init__(
            self,
            *args,
            preprocessors: list[A2ARequestPreprocessor] | None = None,
            ownership_provider: OwnershipProvider | None = None,
            event_listener: TaskEventListener | None = None,
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

    @override
    @_with_preprocessors
    async def _setup_active_task(
            self,
            params: SendMessageRequest,
            call_context: ServerCallContext,
    ) -> tuple[ActiveTask, RequestContext]:
        """Setup the active task registry with preprocessors."""
        self._verify_declared_extensions(params, call_context)
        return await super()._setup_active_task(params, call_context)

    def _verify_declared_extensions(
            self,
            params: SendMessageRequest,
            call_context: ServerCallContext,
    ) -> None:
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
        )
        try:
            AionRuntimeExtensions.collect(
                declaration, aion_a2a_extension_registry.get_all()
            )
        except ExtensionActivationError as ex:
            raise InvalidParamsError(message=str(ex)) from ex

    @staticmethod
    async def on_get_context(
            params: GetContextParams,
            context: ServerCallContext | None = None
    ) -> Conversation:
        """Get conversation context by ID.

        Args:
            params: Parameters containing context ID
            context: Optional server call context

        Returns:
            Conversation object with context data
        """
        task_store = store_manager.get_store()
        tasks = await task_store.get_context_tasks(
            context_id=params.context_id,
            limit=params.history_length,
            offset=params.history_offset)

        return ConversationBuilder.build_from_tasks(context_id=params.context_id, tasks=tasks)

    @staticmethod
    async def on_get_contexts_list(
            params: GetContextsListParams,
            context: ServerCallContext | None = None
    ) -> ContextsList:
        """Get list of available context IDs.

        Args:
            params: Parameters for contexts list request
            context: Optional server call context

        Returns:
            List of available context IDs
        """
        task_store = store_manager.get_store()
        context_ids = await task_store.get_context_ids(
            limit=params.history_length,
            offset=params.history_offset)
        return ContextsList.model_validate(context_ids)

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

    @validate_request_params
    async def on_cancel_task(
            self,
            params: CancelTaskRequest,
            context: ServerCallContext,
    ) -> Task:
        """Cancel a task, wherever in the process fleet it is actually executing.

        Three branches, tried in order:

        1. This process is executing the task right now
           (``AionActiveTaskRegistry.cancel_local``). Cancelled through the
           ordinary A2A executor path - rescue included, for evolution runs -
           and the resulting terminal task is returned directly with no
           further waiting.
        2. Another process holds a live claim
           (``BaseTaskStore.request_cancellation``). This process cannot run
           that owner's teardown itself, so it marks the claim and waits,
           bounded, for the terminal write the owner's own cancellation - now
           discovered over ``TASK_EVENT_CHANNEL`` rather than only at its next
           heartbeat renewal - will produce. See ``TaskEventListener`` and
           ``AionActiveTaskRegistry._on_control_signal``. A wait that outruns
           the bound answers with the task as currently stored rather than
           blocking the caller further: the eventual terminal state still
           reaches the client later, over push or ``tasks/resubscribe``.
        3. No live claim exists anywhere. There is no owner to ask, so the
           terminal state is written directly
           (``BaseTaskStore.cancel_with_ownership_revocation``), exactly as
           this method used to do unconditionally.

        In every branch, the already-terminal case is reported as an error
        rather than inferred from the returned state: a successful
        cancellation is itself terminal, so afterwards the two would be
        indistinguishable.
        """
        task_id = params.id

        local = await self._active_task_registry.cancel_local(task_id, context)
        if local is not None:
            return local

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
                raise TaskNotFoundError

            if marked:
                if waiter is not None:
                    await waiter.wait(timeout=CANCEL_WAIT_SECONDS)
                # Read regardless of whether the wait was woken or timed out:
                # a woken wait still needs the settled row, and a timed-out
                # one may have missed a notification lost to a listener
                # reconnect rather than a cancellation that never happened.
                task = await self.task_store.get(task_id, context)
                if task is None:
                    raise TaskNotFoundError
                return task
        finally:
            if waiter is not None:
                waiter.release()

        task = await self.task_store.cancel_with_ownership_revocation(
            task_id,
            context,
        )
        if task is None:
            raise TaskNotFoundError
        return task

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
        """
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
