"""The Context extension's three methods, independent of the transport that carries them.

``ContextService`` answers ``GetContexts``, ``GetContext`` and
``DeleteContext`` for one agent. Visibility and storage come from a
``ContextCatalog``; this module adds who the caller is, what the answer looks
like, and how a deletion is driven to its end.

**Who the caller is.** A caller is identified by its owner scope - the same
value its tasks are owned by and its context binding is keyed by. A caller
without individual access (an unattributed token, whose subject every such
caller shares) or without an owner scope sees no context and deletes none.

**Deleting.** ``DeleteContext`` removes the caller's binding. While other
callers are bound, that is all it does. Removing the last binding makes the
deletion durable - the context is fenced as ``deleting``, which admits no new
work - and then:

1. every active task of the context, whoever owns it, is cancelled through the
   ordinary cancellation path, in bounded groups;
2. once all of them are terminal, the framework state of the context is
   deleted - LangGraph checkpoints, ADK sessions - under the context holder's
   state scope;
3. the context's tasks, messages, artifacts, push configurations and bindings
   are removed in one storage step, and the context is ``deleted``.

Any of these can be interrupted - a cancellation still settling, a timeout, a
restart. The deletion stays durable, the request answers
``ContextDeletionInProgress``, and the same caller's retry resumes it; a
server also resumes every pending deletion when it starts. A definitive
refusal - a cancellation the agent rejects, or framework state the executor
cannot delete - returns the context to ``active`` and answers
``ContextNotDeletable``; the removed binding stays removed.

A ``deleted`` context left nothing behind, so the next request reusing its ID
starts a new conversation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Optional

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver
from a2a.utils.errors import A2AError, TaskNotCancelableError, UnsupportedOperationError

from aion.core.a2a import (
    ContextArtifact,
    ContextSummary,
    ContextSummaryList,
    ContextView,
    DeleteContextParams,
    DeleteContextResult,
    GetContextParams,
    GetContextsParams,
)
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.auth import has_individual_access
from aion.server.tasks.admission import ContextHolder
from aion.server.tasks.contexts import ContextCatalog, DeletionStep, DeletionTicket, FinishOutcome

from .errors import ContextDeletionInProgress, ContextNotDeletable, ContextNotFound

__all__ = [
    "CANCEL_GROUP_SIZE",
    "ContextService",
    "ContextStateDeleter",
]

logger = logging.getLogger(__name__)

CANCEL_GROUP_SIZE = 10
"""Tasks cancelled concurrently by one deletion; the next group starts when this one settles."""


class ContextStateDeleter:
    """The framework side of a deletion: whether state can be deleted, and deleting it.

    Wraps the running agent so the service needs nothing but these two
    questions from it.
    """

    def __init__(
        self,
        supported: Callable[[], bool],
        delete: Callable[..., Awaitable[None]],
    ) -> None:
        self._supported = supported
        self._delete = delete

    @property
    def supported(self) -> bool:
        """Whether the framework can delete a context's state."""
        return self._supported()

    async def delete(self, context_id: str, holder: Optional[ContextHolder]) -> None:
        """Delete the state of a context under its holder's state scope.

        A ``blocked`` context was used before reservations recorded who held
        it, so there is no state owner to key its state by: nothing is
        deleted for it.
        """
        if holder is None or holder.kind == "blocked":
            return
        gateway = None
        if holder.kind == "shared":
            gateway = (holder.owner_agent_identity_id, holder.edge_agent_environment_id)
        await self._delete(context_id, owner_scope=holder.owner_scope, gateway=gateway)


class ContextService:
    """``GetContexts``, ``GetContext`` and ``DeleteContext`` for one agent.

    Args:
        catalog: Context visibility and storage, chosen with the task store.
        owner_resolver: The agent's owner resolver - the one its tasks and
            bindings are keyed by.
        cancel_task: Cancels one task whoever owns it, through the ordinary
            cancellation path; raises ``TaskNotCancelableError`` for a task
            that already has an outcome.
        state_deleter: Deletes a context's framework state.
        forget_push_configs: Removes push configurations of removed tasks
            that the catalog's storage step does not reach.
    """

    def __init__(
        self,
        catalog: ContextCatalog,
        owner_resolver: OwnerResolver,
        cancel_task: Callable[[str], Awaitable[object]],
        state_deleter: ContextStateDeleter,
        forget_push_configs: Callable[[Sequence[str]], Awaitable[None]] | None = None,
    ) -> None:
        self._catalog = catalog
        self._owner_resolver = owner_resolver
        self._cancel_task = cancel_task
        self._state_deleter = state_deleter
        self._forget_push_configs = forget_push_configs

    def member_of(self, call_context: Optional[ServerCallContext]) -> Optional[str]:
        """The owner scope a caller is bound by; ``None`` for a caller that cannot be bound."""
        if call_context is None or not has_individual_access(call_context):
            return None
        return self._owner_resolver(call_context) or None

    async def get_contexts(
        self, params: GetContextsParams, call_context: Optional[ServerCallContext]
    ) -> ContextSummaryList:
        """The caller's contexts, most recently active first."""
        member = self.member_of(call_context)
        if member is None:
            return ContextSummaryList([])
        activities = await self._catalog.list_contexts(
            member, limit=params.history_length, offset=params.history_offset
        )
        return ContextSummaryList(
            [
                ContextSummary(context_id=activity.context_id, last_activity_at=activity.last_activity_at)
                for activity in activities
            ]
        )

    async def get_context(
        self, params: GetContextParams, call_context: Optional[ServerCallContext]
    ) -> ContextView:
        """One context the caller can see.

        Raises:
            ContextNotFound: The context does not exist or the caller is not
                bound to it; the two are not told apart.
        """
        member = self.member_of(call_context)
        page = None
        if member is not None:
            page = await self._catalog.read_context(
                params.context_id,
                member,
                history_length=params.history_length,
                history_offset=params.history_offset,
            )
        if page is None:
            raise ContextNotFound(params.context_id)
        return ContextView(
            context_id=page.context_id,
            history=page.history,
            artifacts=[ContextArtifact(task_id=task_id, artifact=artifact) for task_id, artifact in page.artifacts],
            status=page.status,
            last_activity_at=page.last_activity_at,
        )

    async def delete_context(
        self, params: DeleteContextParams, call_context: Optional[ServerCallContext]
    ) -> DeleteContextResult:
        """Remove the caller's access, deleting the context when it was the last caller.

        Succeeds the same way for a context that does not exist or that the
        caller cannot see, so the answer discloses nothing about other
        callers.

        Raises:
            ContextDeletionInProgress: The deletion is durable but not
                finished; retry the same request.
            ContextNotDeletable: The deletion was refused definitively and
                the context is active again.
        """
        context_id = params.context_id
        result = DeleteContextResult(context_id=context_id)
        member = self.member_of(call_context)
        if member is None:
            return result

        ticket = await self._catalog.begin_deletion(context_id, member)
        if ticket.step in (DeletionStep.NOT_VISIBLE, DeletionStep.UNBOUND):
            return result
        logger.info(
            "Context deletion %s (%s)",
            "started" if ticket.step is DeletionStep.STARTED else "resumed",
            ticket.operation_id,
        )
        await self._drive(context_id, ticket)
        return result

    async def resume_pending_deletions(self) -> None:
        """Drive every deletion left ``deleting`` - by a restart, or a caller that never retried.

        Runs without a caller: the deletion is already durable, and finishing
        it needs nothing a caller would add. Each outcome is logged; a
        deletion that still cannot finish stays ``deleting`` for the next
        attempt.
        """
        for context_id, ticket in await self._catalog.pending_deletions():
            try:
                await self._drive(context_id, ticket)
            except ContextDeletionInProgress:
                logger.info("Context deletion %s is still in progress", ticket.operation_id)
            except ContextNotDeletable:
                logger.warning("Context deletion %s was refused; the context is active again", ticket.operation_id)
            except Exception:
                logger.exception("Context deletion %s could not be resumed", ticket.operation_id)

    async def _drive(self, context_id: str, ticket: DeletionTicket) -> None:
        """Take a durable deletion as far as it can go in this request."""
        operation_id = ticket.operation_id
        assert operation_id is not None

        if not self._state_deleter.supported:
            # Refused before any task is cancelled: framework state that
            # would survive the deletion could not be deleted at all.
            await self._catalog.abandon_deletion(context_id, operation_id)
            raise ContextNotDeletable(context_id, deletion_operation_id=operation_id)

        try:
            refused = await self._cancel_active_tasks(context_id, operation_id)
        except ContextDeletionInProgress:
            raise
        except Exception as error:
            logger.warning("Context deletion %s could not settle its tasks: %s", operation_id, error)
            raise ContextDeletionInProgress(context_id, deletion_operation_id=operation_id) from error
        if refused:
            await self._catalog.abandon_deletion(context_id, operation_id)
            raise ContextNotDeletable(context_id, deletion_operation_id=operation_id)

        try:
            await self._state_deleter.delete(context_id, ticket.holder)
        except UnsupportedOperationError as error:
            await self._catalog.abandon_deletion(context_id, operation_id)
            raise ContextNotDeletable(context_id, deletion_operation_id=operation_id) from error
        except Exception as error:
            logger.warning("Context deletion %s could not delete framework state: %s", operation_id, error)
            raise ContextDeletionInProgress(context_id, deletion_operation_id=operation_id) from error

        outcome, removed = await self._catalog.finish_deletion(context_id, operation_id)
        if outcome is not FinishOutcome.FINISHED:
            # Not settled yet, or superseded by a concurrent attempt at the
            # same deletion that finished or abandoned it: only the next
            # attempt can tell this request which, and it will.
            raise ContextDeletionInProgress(context_id, deletion_operation_id=operation_id)
        logger.info("Context deletion %s finished; %d task(s) removed", operation_id, len(removed))
        if removed and self._forget_push_configs is not None:
            try:
                await self._forget_push_configs(removed)
            except Exception:
                logger.exception("Context deletion %s could not remove push configurations", operation_id)

    async def _cancel_active_tasks(self, context_id: str, operation_id: str) -> bool:
        """Cancel every active task of the context, in bounded groups.

        Every attempt is waited for, refused or not, before this returns: a
        refusal must not leave cancellations running behind a context that
        is about to be active again.

        Returns:
            Whether a cancellation was refused definitively while some task
            is still active.

        Raises:
            ContextDeletionInProgress: Some task is still active and nothing
                refused to cancel it - its cancellation is still settling.
        """
        tasks = await self._catalog.context_tasks(context_id)
        active = [task.task_id for task in tasks if task.state not in TERMINAL_TASK_STATES]
        refused = False
        for group in _groups(active, CANCEL_GROUP_SIZE):
            outcomes = await asyncio.gather(*(self._cancel_task(task_id) for task_id in group), return_exceptions=True)
            for task_id, outcome in zip(group, outcomes):
                if not isinstance(outcome, BaseException) or isinstance(outcome, TaskNotCancelableError):
                    continue
                if isinstance(outcome, A2AError):
                    logger.warning("Cancellation of task %s was refused: %s", task_id, outcome)
                    refused = True
                else:
                    logger.warning("Cancellation of task %s did not settle: %s", task_id, outcome)

        if not active:
            return False
        still_active = [
            task.task_id
            for task in await self._catalog.context_tasks(context_id)
            if task.state not in TERMINAL_TASK_STATES
        ]
        if not still_active:
            return False
        if refused:
            return True
        raise ContextDeletionInProgress(context_id, deletion_operation_id=operation_id)


def _groups(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]
