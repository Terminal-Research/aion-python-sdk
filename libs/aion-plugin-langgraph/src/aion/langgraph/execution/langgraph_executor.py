"""LangGraph executor — orchestrates stream, state retrieval, and result handling."""

from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.shared.agent.adapters import (
    ExecutionConfig,
    ExecutionSnapshot,
    ExecutorAdapter,
)
from aion.shared.agent.exceptions import ExecutionError, StateRetrievalError
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger

from ..state import LangGraphStateAdapter
from .a2a_converter import LangGraphA2AConverter
from .event_preprocessor import LangGraphEventPreprocessor
from .result_handler import ExecutionResultHandler
from .stream_executor import StreamExecutor, StreamResult
from .transformer import LangGraphTransformer

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()


class LangGraphExecutor(ExecutorAdapter):
    """Orchestrator for LangGraph agent execution."""

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
        self._state_adapter = LangGraphStateAdapter()
        self._result_handler = result_handler or ExecutionResultHandler()
        self._preprocessor = LangGraphEventPreprocessor()

    async def stream(
        self,
        context: "RequestContext",
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the graph from scratch and stream A2A events."""
        converter = LangGraphA2AConverter(
            task_id=context.task_id,
            context_id=context.context_id,
        )
        try:
            lg_inputs = LangGraphTransformer.transform_context(context)
            lg_config = LangGraphTransformer.to_langgraph_config(config)

            stream_exec = StreamExecutor(self.compiled_graph, converter, self._preprocessor)
            async for a2a_event in stream_exec.execute(lg_inputs, lg_config):
                yield a2a_event

            async for a2a_event in self._finalize(stream_exec.result, config, context, converter):
                yield a2a_event

        except Exception as e:
            logger.error(f"LangGraph stream failed: {e}")
            yield converter.convert_error(str(e), type(e).__name__)
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
            state = await self.get_state(config)

            if not state.requires_input():
                logger.warning(
                    f"Attempted to resume non-interrupted execution: {config.context_id}"
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
            lg_inputs = LangGraphTransformer.transform_context(context)
            resume_command = self._state_adapter.create_resume_input(lg_inputs, state)
            lg_config = LangGraphTransformer.to_langgraph_config(config)

            logger.debug(f"Resuming task")

            stream_exec = StreamExecutor(self.compiled_graph, converter, self._preprocessor)
            async for a2a_event in stream_exec.execute(resume_command, lg_config):
                yield a2a_event

            async for a2a_event in self._finalize(stream_exec.result, config, context, converter):
                yield a2a_event

        except Exception as e:
            logger.error(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    async def get_state(self, config: ExecutionConfig) -> ExecutionSnapshot:
        """Return the current graph state as an ExecutionSnapshot."""
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            lg_config = LangGraphTransformer.to_langgraph_config(config)
            snapshot = await self.compiled_graph.aget_state(lg_config)
            return self._state_adapter.get_state_from_snapshot(snapshot)

        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def _finalize(
        self,
        stream_result: StreamResult,
        config: Optional[ExecutionConfig],
        context: "RequestContext",
        converter: LangGraphA2AConverter,
    ) -> AsyncIterator[AgentEvent]:
        """Emit result events and the terminal complete or interrupt event."""
        snapshot = await self.get_state(config)

        for a2a_event in self._result_handler.handle(
            stream_result, snapshot, context,
            task_id=context.task_id,
            context_id=context.context_id,
        ):
            yield a2a_event

        if snapshot.requires_input():
            yield converter.convert_interrupt(
                self._state_adapter.extract_all_interrupts(snapshot)
            )
        else:
            yield converter.convert_complete()
