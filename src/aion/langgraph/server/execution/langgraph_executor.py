"""LangGraph executor — orchestrates stream, resume, and result handling."""
from __future__ import annotations
import logging
import time

from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.core.a2a import reply_of
from aion.core.config.models import AgentConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, Interrupt, StateSnapshot
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from aion.server.agent.adapters import (
    ExecutionConfig,
    AgentFrameworkExecutor,
    LegacyStateError,
)
from aion.server.agent.exceptions import ExecutionError, StateRetrievalError
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.a2a.utils import empty_input_warning, extract_input_preview
from .event_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor
from .result_handler import ExecutionResultHandler
from .stream_executor import StreamExecutor, StreamResult
from .transformer import LangGraphTransformer

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.runtime import AionRuntimeContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Task | Message

logger = logging.getLogger(__name__)


class LangGraphExecutor(AgentFrameworkExecutor):
    """Orchestrator for LangGraph agent execution."""

    framework = "langgraph"

    def __init__(
            self,
            compiled_graph: Any,
            config: AgentConfig,
            result_handler: Optional[ExecutionResultHandler] = None,
    ):
        """
        Args:
            compiled_graph: A compiled LangGraph StateGraph (or CompiledGraph).
            config: Agent-level configuration (model params, tools, etc.).
            result_handler: Optional handler for post-stream result processing.
                Defaults to a plain ExecutionResultHandler.
        """
        self.compiled_graph = compiled_graph
        self.config = config
        self._result_handler = result_handler or ExecutionResultHandler()
        self._preprocessor = LangGraphEventPreprocessor()

    async def stream(
            self,
            context: "RequestContext",
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the graph from scratch and stream A2A events."""
        context_id = context.context_id
        converter = LangGraphA2AConverter(
            task_id=context.task_id,
            context_id=context_id,
        )
        start = time.monotonic()
        try:
            # The input text itself stays on DEBUG: an INFO record travels to
            # logstash, and the user's own words are already kept in the task
            # store under this very context_id.
            logger.info(
                f"Starting LangGraph stream: context_id={context_id}"
                f"{empty_input_warning(context.message)}"
            )
            logger.debug(f"Input preview: {extract_input_preview(context.message)!r}")

            lg_inputs = LangGraphTransformer.generate_langgraph_inputs(context)
            lg_config = LangGraphTransformer.generate_langgraph_config(config)
            if lg_config:
                snapshot = await self.compiled_graph.aget_state(lg_config)
                if not snapshot.values and not snapshot.next:
                    await self._refuse_legacy_state(config)

            runtime_context = await AionRuntimeContextRegistry.aget_current_context()
            stream_exec = StreamExecutor(self.compiled_graph, converter, self._preprocessor)
            events_generator = stream_exec.execute(
                lg_inputs, lg_config,
                runtime_context=runtime_context
            )
            async for a2a_event in events_generator:
                yield a2a_event

            async for a2a_event in self._finalize(stream_exec.result, config, context, converter):
                yield a2a_event

            logger.info(f"LangGraph stream completed: context_id={context_id}, duration={time.monotonic() - start:.3f}s")

        except Exception as e:
            # The request executor decides what the task says and logs the
            # failure once; this adds only how long the run lasted.
            logger.debug(f"LangGraph stream failed after {time.monotonic() - start:.3f}s: {e}")
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def resume(
            self,
            context: "RequestContext",
            config: ExecutionConfig,
    ) -> AsyncIterator[AgentEvent]:
        """Resume a previously interrupted graph execution and stream A2A events."""
        if not config or not config.context_id:
            raise ValueError("context_id is required to resume execution")

        try:
            logger.info(f"Resuming execution for context: {config.context_id}")
            snapshot = await self._graph_state(config)
            pending = pending_interrupts(snapshot)

            if not pending:
                # The task waits without an interrupt - an outbox Task left it
                # waiting for input - so the message is the next turn of it.
                logger.info(
                    f"Task waits without an interrupt; the message runs as a new turn: {config.context_id}"
                )
                user_input = context.get_user_input()
                if not user_input:
                    raise ValueError(
                        f"Execution {config.context_id} is not interrupted, "
                        "but no new inputs provided"
                    )
                async for a2a_event in self.stream(context, config):
                    yield a2a_event
                return

            converter = LangGraphA2AConverter(
                task_id=context.task_id,
                context_id=context.context_id,
            )
            answered = _answered_interrupt(context, pending)
            if answered is None:
                # The message answers an interrupt that no longer waits:
                # LangGraph would drop the answer and stop on the same
                # question, so the graph is not resumed and the question that
                # waits is asked again.
                logger.warning(
                    f"The message answers interrupt {_named_interrupt_id(context)!r}, which is not waiting; "
                    f"asking interrupt {pending[0].id!r} again"
                )
                yield converter.convert_interrupt(pending)
                return
            # The answer goes by the interrupt's id even when one interrupt
            # waits: LangGraph takes a dict whose keys all look like interrupt
            # ids - an empty one among them - for a map of answers by id, and
            # would resume nothing with the user's {} as a plain value.
            resume_command = Command(resume={answered.id: resume_value(context.message)})
            lg_config = LangGraphTransformer.generate_langgraph_config(config)

            logger.debug(f"Resuming task")

            runtime_context = await AionRuntimeContextRegistry.aget_current_context()
            stream_exec = StreamExecutor(self.compiled_graph, converter, self._preprocessor)
            events_generator = stream_exec.execute(
                resume_command, lg_config,
                runtime_context=runtime_context
            )
            async for a2a_event in events_generator:
                yield a2a_event

            async for a2a_event in self._finalize(stream_exec.result, config, context, converter):
                yield a2a_event

        except Exception as e:
            logger.debug(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    @property
    def supports_state_deletion(self) -> bool:
        """Whether the graph's checkpointer can delete a thread.

        A graph without a checkpointer keeps no state. One compiled by the
        application with its own checkpointer can delete only if that
        checkpointer implements ``adelete_thread``; the base saver's raises.
        """
        checkpointer = getattr(self.compiled_graph, "checkpointer", None)
        if not isinstance(checkpointer, BaseCheckpointSaver):
            return True
        return type(checkpointer).adelete_thread is not BaseCheckpointSaver.adelete_thread

    async def delete_state(self, config: ExecutionConfig) -> None:
        """Delete the context's checkpoint thread, keyed as every execution of it was."""
        checkpointer = getattr(self.compiled_graph, "checkpointer", None)
        if not isinstance(checkpointer, BaseCheckpointSaver):
            return
        thread_id = config.require_state_scope().key_for(config.context_id)
        await checkpointer.adelete_thread(thread_id)

    async def _graph_state(self, config: ExecutionConfig) -> StateSnapshot:
        """Read the graph's state for this context under the caller's state scope.

        The snapshot is LangGraph's own: ``interrupts`` holds what the graph
        stopped on, empty once a run has finished. When the scoped thread is
        empty, state saved under the bare ``context_id`` is refused as in
        ``stream``.

        Raises:
            ValueError: The configuration names no ``context_id``.
            StateRetrievalError: The state could not be read, or the context
                holds state that predates scoped keys.
        """
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            lg_config = LangGraphTransformer.generate_langgraph_config(config)
            snapshot = await self.compiled_graph.aget_state(lg_config)
            if not snapshot.values and not snapshot.next:
                await self._refuse_legacy_state(config)
            return snapshot

        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def _refuse_legacy_state(self, config: ExecutionConfig) -> None:
        """Refuse a context whose only state was saved under the context_id alone.

        Called once the caller's scoped key is known to hold nothing. State
        under the bare ``context_id`` was saved before keys carried agent and
        owner, so it cannot be attributed to this caller: reading it could
        show one user another's conversation, and starting afresh would drop
        a conversation without a word. Neither happens - the turn fails with
        ``LegacyStateError`` and that state is left untouched.
        """
        legacy_config = LangGraphTransformer.legacy_langgraph_config(config)
        if legacy_config is None:
            return
        legacy = await self.compiled_graph.aget_state(legacy_config)
        if legacy.values or legacy.next:
            raise LegacyStateError(
                f"context {config.context_id} has graph state that predates per-agent, "
                "per-owner state keys and cannot be attributed to this caller; it is "
                "left untouched until it is migrated or removed"
            )

    async def _finalize(
            self,
            stream_result: StreamResult,
            config: Optional[ExecutionConfig],
            context: "RequestContext",
            converter: LangGraphA2AConverter,
    ) -> AsyncIterator[AgentEvent]:
        """Close a streaming section the run left open, then emit result events and the terminal complete or interrupt event."""
        for a2a_event in converter.close_stream():
            yield a2a_event

        snapshot = await self._graph_state(config)

        for a2a_event in self._result_handler.handle(
                stream_result, snapshot, context,
                task_id=context.task_id,
                context_id=context.context_id,
        ):
            yield a2a_event

        pending = pending_interrupts(snapshot)
        if pending:
            yield converter.convert_interrupt(pending)
        else:
            yield converter.convert_complete()


def resume_value(message: Optional[Message]) -> Any:
    """What ``interrupt()`` returns for the user's reply to its question.

    The reply is read by the rule every adapter follows
    (``aion.core.a2a.reply_of``): its data part's value when it has one,
    else the ``actionId`` of the card button it presses, else its text - so a
    graph reads the answer as LangGraph documents it, ``interrupt(...) ==
    "yes"`` or ``interrupt(...)["decisions"]``. Files sent with the reply do
    not change the value: they are the files of the turn's message, which the
    node reads as on any turn, through
    ``Thread.from_context(runtime.context).message.files``.
    """
    return reply_of(message).value


INTERRUPT_ID_KEY = "interruptId"
"""Where a message names the interrupt it answers, in its metadata: the key the question carries its id under."""


def pending_interrupts(snapshot: StateSnapshot) -> list[Interrupt]:
    """The interrupts of the graph that still wait for an answer, in LangGraph's order.

    Several nodes of one step may interrupt at once. An answer to one of them
    lets its node finish, and the others keep waiting. LangGraph 1.0 still
    lists the answered interrupt in ``StateSnapshot.interrupts`` then, so an
    interrupt waits while its task has no result; a snapshot whose tasks
    name none falls back to ``interrupts``.
    """
    waiting = [
        interrupt
        for task in snapshot.tasks
        if getattr(task, "result", None) is None
        for interrupt in task.interrupts
    ]
    return waiting or list(snapshot.interrupts)


def _named_interrupt_id(context: "RequestContext") -> Optional[str]:
    """The interrupt the message names under ``interruptId`` in its metadata, if any."""
    message = context.message
    if message is None or not message.HasField("metadata"):
        return None
    named = message.metadata.fields.get(INTERRUPT_ID_KEY)
    if named is None or named.WhichOneof("kind") != "string_value" or not named.string_value:
        return None
    return named.string_value


def _answered_interrupt(context: "RequestContext", pending: list[Interrupt]) -> Optional[Interrupt]:
    """The waiting interrupt the message answers: the one it names, else the first; None when it names one that does not wait."""
    named = _named_interrupt_id(context)
    if named is None:
        return pending[0]
    return next((interrupt for interrupt in pending if interrupt.id == named), None)
