from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.aion_config.models import AgentConfig
from aion.shared.logging import get_logger

from aion.server.adapters.base.executor_adapter import (
    ExecutionConfig,
    ExecutionEvent,
    ExecutorAdapter,
)
from aion.server.adapters.base.state_adapter import AgentState
from aion.server.adapters.exceptions import ExecutionError, StateRetrievalError
from aion.server.adapters.langgraph.message_handler import LangGraphMessageAdapter
from aion.server.adapters.langgraph.state_provider import LangGraphStateAdapter

logger = get_logger()

class LangGraphExecutor(ExecutorAdapter):
    def __init__(self, compiled_graph: Any, config: AgentConfig):
        self.compiled_graph = compiled_graph
        self.config = config
        self.state_adapter = LangGraphStateAdapter()
        self.message_adapter = LangGraphMessageAdapter()

    async def invoke(
            self,
            inputs: dict[str, Any],
            config: Optional[ExecutionConfig] = None,
    ) -> dict[str, Any]:
        try:
            langgraph_config = self._to_langgraph_config(config)
            logger.debug(
                f"Invoking LangGraph agent with inputs: {inputs}, config: {langgraph_config}"
            )

            result = await self.compiled_graph.ainvoke(inputs, langgraph_config)
            logger.debug(f"LangGraph invoke completed: {result}")

            return result

        except Exception as e:
            logger.error(f"LangGraph invoke failed: {e}")
            raise ExecutionError(f"Failed to invoke agent: {e}") from e

    async def stream(
            self,
            inputs: dict[str, Any],
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        try:
            langgraph_config = self._to_langgraph_config(config)
            session_id = config.session_id if config else "unknown"

            logger.info(f"Starting LangGraph stream: session_id={session_id}")
            logger.debug(
                f"Stream inputs: {inputs}, config: {langgraph_config}"
            )
            async for event_type, event_data in self.compiled_graph.astream(
                    inputs,
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
                unified_event = self._convert_event(
                    event_type, event_data, metadata
                )

                if unified_event:
                    yield unified_event
            logger.info(f"LangGraph stream completed: session_id={session_id}")
            final_state = await self.get_state(config)
            yield ExecutionEvent(
                event_type="complete",
                data=final_state.values,
                metadata={
                    "is_interrupted": final_state.is_interrupted,
                    "next_steps": final_state.next_steps,
                },
            )

        except Exception as e:
            logger.error(f"LangGraph stream failed: {e}")
            yield ExecutionEvent(
                event_type="error",
                data={"error": str(e), "type": type(e).__name__},
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
            inputs: Optional[dict[str, Any]],
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
                async for event in self.stream(inputs or {}, config):
                    yield event
                return
            resume_input = self.state_adapter.create_resume_input(inputs, state)

            logger.debug(f"Resume input: {resume_input}")
            async for event in self.stream(resume_input, config):
                yield event

        except Exception as e:
            logger.error(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    def supports_streaming(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    def supports_state_retrieval(self) -> bool:
        return True

    def _to_langgraph_config(
            self, config: Optional[ExecutionConfig]
    ) -> dict[str, Any]:
        if not config:
            return {}

        thread_id = config.session_id or config.thread_id
        if not thread_id:
            return {}

        return {"configurable": {"thread_id": thread_id}}

    def _convert_event(
            self,
            event_type: str,
            event_data: Any,
            metadata: Optional[Any] = None,
    ) -> Optional[ExecutionEvent]:
        if event_type == "messages":
            return ExecutionEvent(
                event_type="message",
                data=event_data,
                metadata={"langgraph_metadata": metadata} if metadata else {},
            )

        elif event_type == "values":
            return ExecutionEvent(
                event_type="state_update",
                data=event_data,
                metadata={"source": "langgraph_values"},
            )

        elif event_type == "updates":
            return ExecutionEvent(
                event_type="node_update",
                data=event_data,
                metadata={"source": "langgraph_updates"},
            )

        elif event_type == "custom":
            return ExecutionEvent(
                event_type="custom",
                data=event_data,
                metadata={"source": "langgraph_custom"},
            )

        else:
            logger.warning(f"Unknown LangGraph event type: {event_type}")
            return None

