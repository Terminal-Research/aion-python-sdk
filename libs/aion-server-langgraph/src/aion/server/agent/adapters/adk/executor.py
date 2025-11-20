
from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.shared.agent import AgentInput, ExecutionError, StateRetrievalError
from aion.shared.agent.adapters import AgentState
from aion.shared.agent.adapters import (
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutorAdapter,
)
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger

logger = get_logger()


class ADKExecutor(ExecutorAdapter):

    def __init__(self, agent: Any, config: AgentConfig):
        self.agent = agent
        self.config = config

        self._runner = None
        self._event_converter = None
        self._state_adapter = None

    async def invoke(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> dict[str, Any]:
        try:
            logger.debug(f"Invoking ADK agent with inputs: {inputs.text}")

            final_result = {"status": "completed"}

            # Collect all events and extract final result
            async for event in self.stream(inputs, config):
                if event.event_type == "complete":
                    if hasattr(event, "data"):
                        final_result = event.data
                    break

            logger.debug(f"ADK invoke completed: {final_result}")
            return final_result

        except Exception as e:
            logger.error(f"ADK invoke failed: {e}")
            raise ExecutionError(f"Failed to invoke agent: {e}") from e

    async def stream(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        # TODO: Implement ADK streaming execution
        # This is a placeholder that will be implemented in the next step

        session_id = config.session_id if config else "unknown"
        logger.info(f"ADK streaming not yet implemented: session_id={session_id}")

        # Temporary placeholder response
        yield ErrorEvent(
            error="ADK streaming execution not yet implemented",
            error_type="NotImplementedError",
        )

    async def get_state(self, config: ExecutionConfig) -> AgentState:
        if not config or not config.session_id:
            raise ValueError("session_id is required to get state")

        try:
            logger.debug(f"Getting ADK state for session: {config.session_id}")

            # TODO: Implement state retrieval from ADK SessionService
            # This is a placeholder
            raise NotImplementedError("ADK state retrieval not yet implemented")

        except Exception as e:
            logger.error(f"Failed to get ADK state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def resume(
        self,
        inputs: Optional[AgentInput],
        config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        if not config or not config.session_id:
            raise ValueError("session_id is required to resume execution")

        try:
            logger.info(f"Resuming ADK execution for session: {config.session_id}")

            # TODO: Implement resume logic
            # For ADK, this typically means continuing the same session
            # For now, delegate to stream
            async for event in self.stream(inputs, config):
                yield event

        except Exception as e:
            logger.error(f"Failed to resume ADK execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    def supports_streaming(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    def supports_state_retrieval(self) -> bool:
        return True

    def supports_multi_turn(self) -> bool:
        return True

    def supports_tool_calling(self) -> bool:
        return True

    def supports_human_in_loop(self) -> bool:
        return True
