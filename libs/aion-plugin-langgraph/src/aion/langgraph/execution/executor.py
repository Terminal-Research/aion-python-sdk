from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.agent import AgentInput, ExecutionError, StateRetrievalError
from aion.shared.agent.adapters import (
    CompleteEvent,
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutionSnapshot,
    ExecutorAdapter,
    InterruptEvent,
)
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger

from ..events import LangGraphEventConverter
from ..state import LangGraphStateAdapter

logger = get_logger()


class LangGraphExecutor(ExecutorAdapter):
    def __init__(self, compiled_graph: Any, config: AgentConfig):
        self.compiled_graph = compiled_graph
        self.config = config
        self.state_adapter = LangGraphStateAdapter()
        self.event_converter = LangGraphEventConverter()

    async def _execute_and_convert_stream(
            self,
            inputs: Any,
            config: dict[str, Any],
            stream_mode: list[str]
    ) -> AsyncIterator[ExecutionEvent]:
        """
        Executes the LangGraph stream and converts internal events to unified ExecutionEvents.

        Args:
            inputs: The data to pass to astream (could be state updates or a Command object).
            config: LangGraph-specific configuration (thread_id, etc.).
            stream_mode: List of LangGraph streaming modes to subscribe to.

        Yields:
            Converted unified ExecutionEvent objects.
        """
        async for event_type, event_data in self.compiled_graph.astream(
                inputs,
                config,
                stream_mode=stream_mode,
        ):
            # LangGraph returns a tuple (message, metadata) specifically for the "messages" stream mode
            if event_type == "messages":
                event_data, metadata = event_data
            else:
                metadata = None

            # Transform the vendor-specific event into our system's unified event format
            unified_event = self.event_converter.convert(
                event_type, event_data, metadata
            )

            if unified_event:
                yield unified_event

    async def _handle_final_state(self, final_state: ExecutionSnapshot) -> AsyncIterator[ExecutionEvent]:
        """
        Analyzes the final state of the graph to determine if it finished
        normally or was interrupted for user input.

        Args:
            final_state: The snapshot of the graph state after execution.

        Yields:
            Either an InterruptEvent or a CompleteEvent.
        """
        # Check if the graph is paused at a breakpoint or waiting for input
        if final_state.requires_input():
            # Extract all interrupts (LangGraph 0.6.0+ supports multiple)
            all_interrupts = self.state_adapter.extract_all_interrupts(final_state)

            logger.debug(
                f"Execution interrupted: awaiting user input "
                f"({len(all_interrupts)} interrupt(s))"
            )

            yield InterruptEvent(
                interrupts=all_interrupts,
            )
        else:
            yield CompleteEvent()

    async def stream(
            self,
            inputs: AgentInput,
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        try:
            langgraph_config = self._to_langgraph_config(config)
            langgraph_inputs = self._transform_inputs(inputs)

            async for event in self._execute_and_convert_stream(
                    langgraph_inputs,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"]
            ):
                yield event

            final_state = await self.get_state(config)
            async for final_event in self._handle_final_state(final_state):
                yield final_event

        except Exception as e:
            logger.error(f"LangGraph stream failed: {e}")
            yield ErrorEvent(
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def resume(
            self,
            inputs: Optional[AgentInput],
            config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        if not config or not config.context_id:
            raise ValueError("context_id is required to resume execution")

        try:
            logger.info(f"Resuming execution for context: {config.context_id}")
            state = await self.get_state(config)

            if not state.requires_input():
                logger.warning(
                    f"Attempted to resume non-interrupted execution: {config.context_id}"
                )
                # If not interrupted, continue with new inputs or raise error
                if not inputs:
                    raise ValueError(
                        f"Execution {config.context_id} is not interrupted, "
                        "but no new inputs provided"
                    )
                async for event in self.stream(inputs, config):
                    yield event
                return

            # Transform inputs to LangGraph format for resume
            transformed_inputs = self._transform_inputs(inputs) if inputs else None

            # Create Command object for resume (LangGraph-specific)
            resume_command = self.state_adapter.create_resume_input(transformed_inputs, state)

            logger.debug(f"Resuming with command: {resume_command}")

            # Stream using the Command object directly
            langgraph_config = self._to_langgraph_config(config)

            async for event in self._execute_and_convert_stream(
                    resume_command,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"]
            ):
                yield event

            final_state = await self.get_state(config)
            async for final_event in self._handle_final_state(final_state):
                yield final_event

        except Exception as e:
            logger.error(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    async def get_state(self, config: ExecutionConfig) -> ExecutionSnapshot:
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            langgraph_config = self._to_langgraph_config(config)
            snapshot = await self.compiled_graph.aget_state(langgraph_config)
            execution_snapshot = self.state_adapter.get_state_from_snapshot(snapshot)
            return execution_snapshot

        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    @staticmethod
    def _to_langgraph_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Convert ExecutionConfig to LangGraph configuration format.

        Args:
            config: Execution configuration with context_id

        Returns:
            LangGraph config dict with thread_id (mapped from context_id)
        """
        if not config:
            return {}

        if not config.context_id:
            return {}

        return {"configurable": {"thread_id": config.context_id}}

    @staticmethod
    def _transform_inputs(inputs: AgentInput) -> dict[str, Any]:
        """Transform universal AgentInput to LangGraph format.

        Converts AgentInput.text to LangGraph's expected message format.

        Args:
            inputs: Universal agent input

        Returns:
            LangGraph-compatible input dict: {"messages": [("user", text)]}
        """
        return {"messages": [("user", inputs.text)]}
