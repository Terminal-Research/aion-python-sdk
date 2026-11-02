"""Factory for creating AionInvocationContext instances populated from the runtime context."""

import uuid
from typing import Optional

from aion.adk.authoring.invocation import AionInvocationContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from google.adk.agents import BaseAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import ResumabilityConfig
from google.adk.artifacts import BaseArtifactService
from google.adk.sessions import BaseSessionService, Session
from google.genai import types


class AionInvocationContextFactory:
    """Creates AionInvocationContext instances populated from the runtime context registry.

    Every invocation is resumable, as in an ADK ``App`` with
    ``ResumabilityConfig(is_resumable=True)``: a run that makes a
    long-running tool call - ``request_input``, a tool confirmation, a
    ``LongRunningFunctionTool`` - stops there, and the reply resumes the same
    invocation. Without it ADK calls the model again at once and the user is
    never asked.
    """

    def __init__(
            self,
            agent: BaseAgent,
            session_service: BaseSessionService,
            artifact_service: Optional[BaseArtifactService] = None,
            run_config: Optional[RunConfig] = None,
    ) -> None:
        self._agent = agent
        self._session_service = session_service
        self._artifact_service = artifact_service
        self._run_config = run_config if run_config is not None else (
            RunConfig(
                response_modalities=["TEXT"],
                streaming_mode=StreamingMode.SSE,
            )
        )

    def create(
            self,
            session: Session,
            user_content: types.Content,
            *,
            invocation_id: Optional[str] = None,
            agent: Optional[BaseAgent] = None,
    ) -> AionInvocationContext:
        """Build an AionInvocationContext for a single agent invocation.

        Args:
            session: The context's session.
            user_content: What the user sent this turn, as ADK reads it.
            invocation_id: The invocation to continue - the one a paused
                tool call belongs to - or None for a new one.
            agent: The agent that runs, when it is not the root agent: the
                one whose tool call the turn answers.
        """
        return AionInvocationContext(
            invocation_id=invocation_id or str(uuid.uuid4()),
            session_service=self._session_service,
            artifact_service=self._artifact_service,
            agent=agent or self._agent,
            user_content=user_content,
            session=session,
            run_config=self._run_config,
            resumability_config=ResumabilityConfig(is_resumable=True),
            aion_runtime_context=AionRuntimeContextRegistry.get_current_context(),
        )
