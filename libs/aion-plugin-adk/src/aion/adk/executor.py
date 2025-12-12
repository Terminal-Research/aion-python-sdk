
from collections.abc import AsyncIterator
from typing import Any, Optional
import uuid

from google.genai import types
from google.adk.agents import InvocationContext
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService, Session
from google.adk.events import Event

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

from .event_converter import ADKEventConverter
from .state import ADKStateAdapter

logger = get_logger()


class ADKExecutor(ExecutorAdapter):

    def __init__(self, agent: Any, config: AgentConfig):
        self.agent = agent
        self.config = config

        # Initialize session service for managing conversations
        self._session_service = InMemorySessionService()

        # Initialize adapters
        self._event_converter = ADKEventConverter()
        self._state_adapter = ADKStateAdapter()

    async def stream(
        self,
        inputs: AgentInput,
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        """Stream agent execution using ADK's run_async method.

        Args:
            inputs: Universal agent input containing the user message
            config: Execution configuration with context_id

        Yields:
            ExecutionEvent: Unified events converted from ADK Events
        """
        try:
            context_id = config.context_id if config else str(uuid.uuid4())
            logger.info(f"Starting ADK stream: context_id={context_id}")

            # Get or create session
            session = await self._get_or_create_session(context_id)

            # Create user content from inputs
            user_content = self._transform_inputs(inputs)

            # IMPORTANT: Add user message to session BEFORE creating invocation context
            # ADK expects user messages to be in session.events, not just in user_content
            user_event = Event(
                author="user",
                content=user_content,
                partial=False,
            )
            await self._session_service.append_event(session, user_event)
            logger.debug(f"Added user message to session: {user_content}")

            # Create invocation context
            invocation_context = await self._create_invocation_context(
                session=session,
                user_content=user_content,
            )

            logger.debug(f"Stream inputs: {user_content}, context_id: {context_id}")

            # Stream events from ADK agent
            async for adk_event in self.agent.run_async(invocation_context):
                logger.debug(
                    f"ADK stream event: {type(adk_event).__name__}, "
                    f"author={adk_event.author}, partial={adk_event.partial}"
                )

                # Convert ADK event to unified ExecutionEvent
                unified_event = self._event_converter.convert(adk_event)
                if unified_event:
                    yield unified_event

                # Append non-partial events to session
                if not adk_event.partial:
                    await self._session_service.append_event(session, adk_event)

            logger.info(f"ADK stream completed: context_id={context_id}")

            # Get final state to check for interrupts
            final_state = await self.get_state(config)

            # Yield appropriate completion event
            if final_state.is_interrupted:
                interrupt_info = self._state_adapter.extract_interrupt_info(final_state)
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
            logger.error(f"ADK stream failed: {e}", exc_info=True)
            yield ErrorEvent(
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def get_state(self, config: ExecutionConfig) -> AgentState:
        """Retrieve the current state from ADK session.

        Args:
            config: Execution configuration with context_id

        Returns:
            AgentState: Unified agent state with values, next_steps, and interrupt info

        Raises:
            ValueError: If context_id is not provided
            StateRetrievalError: If state retrieval fails
        """
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            logger.debug(f"Getting ADK state for context: {config.context_id}")

            # Get session from session service
            session = await self._session_service.get_session(
                app_name=self._get_app_name(),
                user_id=self._get_user_id(),
                session_id=config.context_id,
            )

            if not session:
                raise StateRetrievalError(
                    f"Session not found: {config.context_id}"
                )

            # Convert ADK session to unified AgentState
            agent_state = self._state_adapter.from_adk_session(session)

            logger.debug(
                f"State retrieved: interrupted={agent_state.is_interrupted}, "
                f"next_steps={agent_state.next_steps}"
            )

            return agent_state

        except Exception as e:
            logger.error(f"Failed to get ADK state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    async def resume(
        self,
        inputs: Optional[AgentInput],
        config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        """Resume an interrupted ADK agent execution.

        Args:
            inputs: Optional user input to provide when resuming
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

            # Check current state
            state = await self.get_state(config)

            if not state.is_interrupted:
                logger.warning(
                    f"Attempted to resume non-interrupted execution: {config.context_id}"
                )
                # If not interrupted, stream with new inputs if provided
                if not inputs:
                    raise ValueError(
                        f"Execution {config.context_id} is not interrupted, "
                        "but no new inputs provided"
                    )
                async for event in self.stream(inputs, config):
                    yield event
                return

            # For ADK, resuming means continuing with the same session
            # The session already contains the conversation history
            # We just need to continue streaming with new inputs
            async for event in self.stream(inputs, config):
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

        # Try to get existing session
        session = await self._session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

        # Create new session if it doesn't exist
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

    def _transform_inputs(self, inputs: AgentInput) -> types.Content:
        """Transform universal AgentInput to ADK Content format.

        Args:
            inputs: Universal agent input

        Returns:
            types.Content: ADK Content object with user message
        """
        return types.Content(
            role="user",
            parts=[types.Part(text=inputs.text)],
        )

    def _get_app_name(self) -> str:
        """Get application name from config or use default.

        Returns:
            str: Application name
        """
        # Use agent name from config or default
        return self.config.name or "aion-adk-agent"

    def _get_user_id(self) -> str:
        """Get user ID from config or use default.

        Returns:
            str: User identifier
        """
        # Use a default user ID for now
        # In production, this should come from authentication context
        return "default-user"
