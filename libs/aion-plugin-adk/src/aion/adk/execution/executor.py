import uuid
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.exceptions import ExecutionError, StateRetrievalError
from aion.shared.agent.adapters import (
    CompleteEvent,
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutionSnapshot,
    ExecutorAdapter,
)
from aion.shared.config.models import AgentConfig
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger
from google.adk.agents import InvocationContext
from google.adk.agents.run_config import RunConfig
from google.adk.events import Event
from google.adk.sessions import Session
from google.genai import types

from ..constants import DEFAULT_USER_ID
from ..events import ADKEventConverter
from ..session import SessionServiceManager
from ..state import StateConverter

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = get_logger()


class ADKExecutor(ExecutorAdapter):

    def __init__(
            self,
            agent: Any,
            config: AgentConfig,
            db_manager: Optional[DbManagerProtocol] = None,
    ):
        self.agent = agent
        self.config = config

        session_manager = SessionServiceManager(db_manager=db_manager)
        self._session_service = session_manager.create_session_service()

        self._event_converter = ADKEventConverter()
        self._state_converter = StateConverter()

    async def stream(
            self,
            context: "RequestContext",
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        """Stream agent execution using ADK's run_async method.

        Args:
            context: A2A request context with message, metadata, and task information
            config: Execution configuration with context_id

        Yields:
            ExecutionEvent: Unified events converted from ADK Events
        """
        try:
            context_id = config.context_id if config else str(uuid.uuid4())
            logger.info(f"Starting ADK stream: context_id={context_id}")

            session = await self._get_or_create_session(context_id)
            user_content = self._transform_context(context)

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

            logger.debug(f"Stream inputs: {user_content}, context_id: {context_id}")

            async for adk_event in self.agent.run_async(invocation_context):
                logger.debug(
                    f"ADK stream event: {type(adk_event).__name__}, "
                    f"author={adk_event.author}, partial={adk_event.partial}"
                )

                unified_event = self._event_converter.convert(adk_event)
                if unified_event:
                    yield unified_event

                if not adk_event.partial:
                    await self._session_service.append_event(session, adk_event)

            logger.info(f"ADK stream completed: context_id={context_id}")

            # ADK doesn't support interrupts, always yield CompleteEvent
            yield CompleteEvent()

        except Exception as e:
            logger.error(f"ADK stream failed: {e}", exc_info=True)
            yield ErrorEvent(
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ExecutionError(f"Failed to stream agent: {e}") from e

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
    ) -> AsyncIterator[ExecutionEvent]:
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
            ExecutionEvent: Events from resumed execution

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
        )

        return InvocationContext(
            invocation_id=invocation_id,
            session_service=self._session_service,
            agent=self.agent,
            user_content=user_content,
            session=session,
            run_config=run_config,
        )

    def _transform_context(self, context: "RequestContext") -> types.Content:
        """Transform A2A RequestContext to ADK Content format.

        Args:
            context: A2A request context

        Returns:
            types.Content: ADK Content object with user message
        """
        user_input = context.get_user_input()
        return types.Content(
            role="user",
            parts=[types.Part(text=user_input)],
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
