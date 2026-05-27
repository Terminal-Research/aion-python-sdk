import uuid
from typing import Optional

from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from google.adk.agents import InvocationContext, BaseAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts import BaseArtifactService
from google.adk.sessions import BaseSessionService, Session
from google.genai import types


class AionInvocationContext(InvocationContext):
    """Extended InvocationContext that carries an Aion runtime context.

    Adds `aion_runtime_context` so that ADK agents can introspect the inbound
    Aion event, distribution, behavior, environment and identity data without
    touching server state directly.
    """

    aion_runtime_context: Optional[AionRuntimeContext] = None


class AionInvocationContextFactory:
    """Creates AionInvocationContext instances populated from the runtime context registry."""

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
    ) -> AionInvocationContext:
        return AionInvocationContext(
            invocation_id=str(uuid.uuid4()),
            session_service=self._session_service,
            artifact_service=self._artifact_service,
            agent=self._agent,
            user_content=user_content,
            session=session,
            run_config=self._run_config,
            aion_runtime_context=AionRuntimeContextRegistry.get_current_context(),
        )
