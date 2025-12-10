from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.agent import AgentInput, ExecutionError, StateRetrievalError
from aion.shared.agent.adapters import AgentState
from aion.shared.agent.adapters import (
    CompleteEvent,
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutorAdapter,
    InterruptEvent,
)
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger

from .event_converter import LangGraphEventConverter
from .state import LangGraphStateAdapter

logger = get_logger()


class LangGraphExecutor(ExecutorAdapter):
    def __init__(self, compiled_graph: Any, config: AgentConfig):
        self.compiled_graph = compiled_graph
        self.config = config
        self.state_adapter = LangGraphStateAdapter()
        self.event_converter = LangGraphEventConverter()

    async def stream(
            self,
            inputs: AgentInput,
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        try:
            langgraph_config = self._to_langgraph_config(config)
            langgraph_inputs = self._transform_inputs(inputs)
            session_id = config.session_id if config else "unknown"

            logger.info(f"Starting LangGraph stream: session_id={session_id}")
            logger.debug(
                f"Stream inputs: {langgraph_inputs}, config: {langgraph_config}"
            )
            async for event_type, event_data in self.compiled_graph.astream(
                    langgraph_inputs,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"],
            ):
                if event_type == "messages":
                    event_data, metadata = event_data
                else:
                    metadata = None

                logger.debug(
                    f"LangGraph stream event [{event_type}]: {type(event_data).__name__}"
                )
                unified_event = self.event_converter.convert(
                    event_type, event_data, metadata
                )
                if unified_event:
                    yield unified_event

            logger.info(f"LangGraph stream completed: session_id={session_id}")
            final_state = await self.get_state(config)

            # Check if execution was interrupted
            if final_state.is_interrupted:
                interrupt_info = self.state_adapter.extract_interrupt_info(final_state)
                yield InterruptEvent(
                    data=final_state.values,
                    next_steps=final_state.next_steps,
                    interrupt=interrupt_info,
                )
            else:
                yield CompleteEvent(
                    data=final_state.values,
                    next_steps=final_state.next_steps,
                )

        except Exception as e:
            logger.error(f"LangGraph stream failed: {e}")
            yield ErrorEvent(
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def get_state(self, config: ExecutionConfig) -> AgentState:
        if not config or not config.session_id:
            raise ValueError("session_id is required to get state")

        try:
            langgraph_config = self._to_langgraph_config(config)
            logger.debug(f"Getting state for session: {config.session_id}")
            snapshot = await self.compiled_graph.aget_state(langgraph_config)
            agent_state = self.state_adapter.get_state_from_snapshot(snapshot)

            logger.debug(
                f"State retrieved: interrupted={agent_state.is_interrupted}, "
                f"next_steps={agent_state.next_steps}"
            )

            return agent_state

        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def resume(
            self,
            inputs: Optional[AgentInput],
            config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        if not config or not config.session_id:
            raise ValueError("session_id is required to resume execution")

        try:
            logger.info(f"Resuming execution for session: {config.session_id}")
            state = await self.get_state(config)

            if not state.is_interrupted:
                logger.warning(
                    f"Attempted to resume non-interrupted execution: {config.session_id}"
                )
                # If not interrupted, continue with new inputs or raise error
                if not inputs:
                    raise ValueError(
                        f"Execution {config.session_id} is not interrupted, "
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
            session_id = config.session_id if config else "unknown"

            async for event_type, event_data in self.compiled_graph.astream(
                    resume_command,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"],
            ):
                if event_type == "messages":
                    event_data, metadata = event_data
                else:
                    metadata = None

                unified_event = self.event_converter.convert(
                    event_type, event_data, metadata
                )
                if unified_event:
                    yield unified_event

            logger.info(f"Resume completed: session_id={session_id}")
            final_state = await self.get_state(config)

            # Check if execution was interrupted again
            if final_state.is_interrupted:
                interrupt_info = self.state_adapter.extract_interrupt_info(final_state)
                yield InterruptEvent(
                    data=final_state.values,
                    next_steps=final_state.next_steps,
                    interrupt=interrupt_info,
                )
            else:
                yield CompleteEvent(
                    data=final_state.values,
                    next_steps=final_state.next_steps,
                )

        except Exception as e:
            logger.error(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    @staticmethod
    def _to_langgraph_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Convert ExecutionConfig to LangGraph configuration format.

        Args:
            config: Execution configuration with session/thread ID

        Returns:
            LangGraph config dict with thread_id
        """
        if not config:
            return {}

        thread_id = config.session_id or config.thread_id
        if not thread_id:
            return {}

        return {"configurable": {"thread_id": thread_id}}

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
