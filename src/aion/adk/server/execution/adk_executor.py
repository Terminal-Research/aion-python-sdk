"""ADK ExecutorAdapter: wires session, artifact, and stream services for a single agent run."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.server.agent.adapters import (
    LegacyStateError,
    ExecutionConfig,
    ExecutionSnapshot,
    ExecutorAdapter,
)
from aion.server.agent.exceptions import ExecutionError, StateRetrievalError
from aion.core.config.models import AgentConfig
from google.adk.artifacts import BaseArtifactService
from google.adk.events import Event
from google.adk.sessions import Session, BaseSessionService

from aion.adk.server.invocation import AionInvocationContextFactory
from aion.adk.server.artifacts import ArtifactServiceFactory
from aion.adk.server.constants import DEFAULT_USER_ID
from aion.server.files.storage import FileUploadManager
from aion.adk.server.session import SessionServiceFactory
from aion.adk.server.state.converter import StateConverter
from aion.adk.server.transformers.a2a_to_adk import ADKTransformer
from aion.server.a2a.utils import empty_input_warning, extract_input_preview
from .event_converter import ADKToA2AEventConverter
from .result_handler import ADKExecutionResultHandler
from .stream_executor import ADKStreamExecutor, ADKStreamResult

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = logging.getLogger(__name__)


class ADKExecutor(ExecutorAdapter):
    """ExecutorAdapter that drives an ADK agent through its run_async lifecycle."""

    def __init__(
            self,
            agent: Any,
            config: AgentConfig,
            session_service: Optional[BaseSessionService] = None,
            artifact_service: Optional[BaseArtifactService] = None,
            result_handler: Optional[ADKExecutionResultHandler] = None,
            file_uploader: Optional[FileUploadManager] = None,
    ):
        self.agent = agent
        self.config = config
        self._session_service = session_service or SessionServiceFactory().create()
        self._artifact_service = artifact_service or ArtifactServiceFactory.create()
        self._result_handler = result_handler or ADKExecutionResultHandler()
        self._file_uploader = file_uploader
        self._state_converter = StateConverter()
        self._invocation_context_factory = AionInvocationContextFactory(
            agent=agent,
            session_service=self._session_service,
            artifact_service=self._artifact_service,
        )

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
        converter = ADKToA2AEventConverter(task_id=task_id, context_id=context_id, file_uploader=self._file_uploader)
        start = time.monotonic()

        try:
            # The input text itself stays on DEBUG: an INFO record travels to
            # logstash, and the user's own words are already kept in the task
            # store under this very context_id.
            logger.info(
                f"Starting ADK stream: context_id={context_id}"
                f"{empty_input_warning(context.message)}"
            )
            logger.debug(f"Input preview: {extract_input_preview(context.message)!r}")

            session = await self._get_or_create_session(config, context_id)
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

            invocation_context = self._invocation_context_factory.create(
                session=session,
                user_content=user_content,
            )

            converter._ctx = invocation_context
            stream_exec = ADKStreamExecutor(self.agent, self._session_service, converter)
            async for a2a_event in stream_exec.execute(invocation_context, session):
                yield a2a_event

            async for a2a_event in self._finalize(
                    stream_exec.result, converter, session, context, task_id, context_id
            ):
                yield a2a_event

            logger.info(f"ADK stream completed: context_id={context_id}, duration={time.monotonic() - start:.3f}s")

        except Exception as e:
            logger.error(f"ADK stream failed ({time.monotonic() - start:.3f}s): {e}", exc_info=True)
            yield converter.generate_error(error=str(e), error_type=type(e).__name__)
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def _finalize(
            self,
            stream_result: ADKStreamResult,
            converter: ADKToA2AEventConverter,
            session: Any,
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """Emit result events and the terminal complete event."""
        for a2a_event in self._result_handler.handle(
                stream_result, converter, session, context, task_id, context_id
        ):
            yield a2a_event

        yield converter.generate_complete()

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

            scope = config.require_state_scope()
            session = await self._session_service.get_session(
                app_name=scope.agent_id,
                user_id=scope.owner_scope,
                session_id=config.context_id,
            )

            if not session:
                await self._refuse_legacy_session(config.context_id)
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

    async def _get_or_create_session(self, config: ExecutionConfig, session_id: str) -> Session:
        """Get the caller's session for this context, or create it.

        Keyed by ``app_name`` = the Aion agent id and ``user_id`` = the
        caller's ``owner_scope`` (``StateScope``), with the A2A ``context_id``
        as ``session_id``: two users - or two agents on one database - that
        present the same ``context_id`` get two sessions.

        Raises:
            LegacyStateError: No such session exists, but one saved before
                sessions were keyed by agent and owner does.
        """
        scope = config.require_state_scope()
        session = await self._session_service.get_session(
            app_name=scope.agent_id,
            user_id=scope.owner_scope,
            session_id=session_id,
        )
        if session:
            logger.debug(f"Session resumed: {session_id}")
            return session

        await self._refuse_legacy_session(session_id)
        logger.info(f"Session created: {session_id}")
        return await self._session_service.create_session(
            app_name=scope.agent_id,
            user_id=scope.owner_scope,
            session_id=session_id,
        )

    async def _refuse_legacy_session(self, session_id: str) -> None:
        """Refuse a context whose only session was saved under the shared user.

        Before sessions were keyed by agent and owner, every user's session
        for a ``context_id`` was ``(display name, "default-user", context_id)``.
        Such a session cannot be attributed to the caller: reading it could
        show one user another's conversation, and creating a fresh one next
        to it would drop a conversation without a word. Neither happens -
        the turn fails with ``LegacyStateError`` and the old session is left
        as it is.
        """
        legacy = await self._session_service.get_session(
            app_name=self._legacy_app_name(),
            user_id=DEFAULT_USER_ID,
            session_id=session_id,
        )
        if legacy is not None:
            raise LegacyStateError(
                f"context {session_id} has an ADK session that predates per-agent, "
                "per-owner session keys and cannot be attributed to this caller; it is "
                "left untouched until it is migrated or removed"
            )

    def _legacy_app_name(self) -> str:
        """The ``app_name`` sessions were saved under before they carried the agent id."""
        return self.config.name or "aion-adk-agent"
