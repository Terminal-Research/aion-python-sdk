import uuid
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
from google.adk.agents import InvocationContext
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.sessions import Session, BaseSessionService
from google.genai import types

from .event_converter import ADKToA2AEventConverter
from .result_handler import ADKExecutionResultHandler
from .stream_executor import ADKStreamExecutor, StreamResult
from aion.adk.transformers import ADKTransformer
from ..constants import DEFAULT_USER_ID
from ..state import StateConverter

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = get_logger()


class ADKExecutor(ExecutorAdapter):

    def __init__(
            self,
            agent: Any,
            config: AgentConfig,
            session_service: BaseSessionService,
            result_handler: Optional[ADKExecutionResultHandler] = None,
    ):
        self.agent = agent
        self.config = config
        self._session_service = session_service
        self._result_handler = result_handler or ADKExecutionResultHandler()
        self._state_converter = StateConverter()

    async def stream(
            self,
            context: "RequestContext",
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent execution using ADK's run_async method.

        Args:
            context: A2A request context with message, metadata, and task information
            config: Execution configuration with context_id

        Yields:
            A2A events converted directly from ADK Events
        """
        task_id = context.task_id or str(uuid.uuid4())
        context_id = ADKTransformer.to_session_id(config) or str(uuid.uuid4())
        converter = ADKToA2AEventConverter(task_id=task_id, context_id=context_id)

        try:
            logger.info(f"Starting ADK stream: context_id={context_id}")

            session = await self._get_or_create_session(context_id)
            user_content = ADKTransformer.transform_context(context)

            # IMPORTANT: Add user message to session BEFORE creating invocation context
            # ADK expects user messages to be in session.events, not just in user_content
            user_event = Event(
                author="user",
                content=user_content,
                partial=False,
            )
            await self._session_service.append_event(session, user_event)
            logger.debug(f"Added user message to session: {user_content}")

            invocation_context = await self._create_invocation_context(
                session=session,
                user_content=user_content,
            )

            stream_exec = ADKStreamExecutor(self.agent, self._session_service, converter)
            async for a2a_event in stream_exec.execute(invocation_context, session):
                yield a2a_event

            async for a2a_event in self._finalize(stream_exec.result, converter):
                yield a2a_event

            logger.info(f"ADK stream completed: context_id={context_id}")

        except Exception as e:
            logger.error(f"ADK stream failed: {e}", exc_info=True)
            yield converter.convert_error(error=str(e), error_type=type(e).__name__)
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def _finalize(
            self,
            stream_result: StreamResult,
            converter: ADKToA2AEventConverter,
    ) -> AsyncIterator[AgentEvent]:
        """Emit result events and the terminal complete event."""
        for a2a_event in self._result_handler.handle(stream_result, converter):
            yield a2a_event

        yield converter.convert_complete()

    async def get_state(self, config: ExecutionConfig) -> ExecutionSnapshot:
        """Retrieve the current execution state snapshot from ADK session.

        Args:
            config: Execution configuration with context_id

        Returns:
            ExecutionSnapshot: Unified execution snapshot with state and messages

        Raises:
            ValueError: If context_id is not provided
            StateRetrievalError: If state retrieval fails
        """
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            logger.debug(f"Getting ADK state for context: {config.context_id}")

            session = await self._session_service.get_session(
                app_name=self._get_app_name(),
                user_id=self._get_user_id(),
                session_id=config.context_id,
            )

            if not session:
                raise StateRetrievalError(
                    f"Session not found: {config.context_id}"
                )

            execution_state = self._state_converter.from_adk_session(session)
            logger.debug(
                f"State retrieved: {len(execution_state.messages)} messages, "
                f"{len(execution_state.state)} state keys"
            )
            return execution_state

        except Exception as e:
            logger.error(f"Failed to get ADK state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def resume(
            self,
            context: "RequestContext",
            config: ExecutionConfig,
    ) -> AsyncIterator[AgentEvent]:
        """Resume ADK agent execution with new user input.

        For ADK, resuming means continuing the conversation with the same session.
        The session already contains the conversation history, and we simply
        stream with new user inputs.

        NOTE: ADK doesn't have explicit interrupt/pause states. Resume is just
        continuing the conversation.

        Args:
            context: A2A request context with message, metadata, and task information
            config: Execution configuration with context_id

        Yields:
            A2A events from resumed execution

        Raises:
            ValueError: If context_id is not provided
            ExecutionError: If resume fails
        """
        if not config or not config.context_id:
            raise ValueError("context_id is required to resume execution")

        try:
            logger.info(f"Resuming ADK execution for context: {config.context_id}")

            async for event in self.stream(context, config):
                yield event

        except Exception as e:
            logger.error(f"Failed to resume ADK execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    async def _get_or_create_session(self, session_id: str) -> Session:
        """Get existing session or create a new one.

        Args:
            session_id: The session identifier

        Returns:
            Session: ADK Session object
        """
        app_name = self._get_app_name()
        user_id = self._get_user_id()

        session = await self._session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

        if not session:
            logger.debug(f"Creating new session: {session_id}")
            session = await self._session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        return session

    async def _create_invocation_context(
            self,
            session: Session,
            user_content: types.Content,
    ) -> InvocationContext:
        """Create an InvocationContext for ADK agent execution.

        Args:
            session: ADK Session object
            user_content: User message content

        Returns:
            InvocationContext: ADK invocation context
        """
        invocation_id = str(uuid.uuid4())

        # Create run config with default settings for text-based interaction
        # response_modalities defaults to None which means AUDIO, so we set it to TEXT
        run_config = RunConfig(
            response_modalities=["TEXT"],
            streaming_mode=StreamingMode.SSE
        )

        return InvocationContext(
            invocation_id=invocation_id,
            session_service=self._session_service,
            agent=self.agent,
            user_content=user_content,
            session=session,
            run_config=run_config,
        )

    def _get_app_name(self) -> str:
        """Get application name from config or use default.

        Returns:
            str: Application name
        """
        return self.config.name or "aion-adk-agent"

    @staticmethod
    def _get_user_id() -> str:
        """Get user ID from config or use default.

        Returns:
            str: User identifier
        """
        return DEFAULT_USER_ID
