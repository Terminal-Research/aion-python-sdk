import uuid
from typing import Optional, TYPE_CHECKING

from aion.shared.types.a2a.models import A2AInbox
from google.adk.agents import InvocationContext, BaseAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts import BaseArtifactService
from google.adk.sessions import BaseSessionService, Session
from google.genai import types

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext


class AionInvocationContext(InvocationContext):
    """Extended InvocationContext that carries an A2A inbox snapshot.

    Adds `a2a_inbox` so that ADK agents can introspect the inbound A2A
    Task / Message / metadata without touching server state directly.
    """

    a2a_inbox: Optional[A2AInbox] = None


class AionInvocationContextFactory:
    """Creates AionInvocationContext instances from A2A request context."""

    def __init__(
            self,
            agent: BaseAgent,
            session_service: BaseSessionService,
            artifact_service: Optional[BaseArtifactService] = None,
            run_config: Optional[RunConfig] = None,
    ) -> None:
        """Initialize the factory with shared ADK dependencies.

        Args:
            agent: The ADK agent that will handle invocations.
            session_service: Service for persisting and retrieving sessions.
            artifact_service: Optional service for artifact storage.
            run_config: Optional run configuration. Defaults to SSE streaming
                with TEXT response modality.
        """
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
            request_context: "RequestContext",
    ) -> AionInvocationContext:
        """Build an AionInvocationContext for a single agent invocation.

        Args:
            session: The active ADK session for this request.
            user_content: The user-provided content to pass to the agent.
            request_context: The A2A request context used to populate the inbox.

        Returns:
            A fully initialised AionInvocationContext ready for agent execution.
        """
        return AionInvocationContext(
            invocation_id=str(uuid.uuid4()),
            session_service=self._session_service,
            artifact_service=self._artifact_service,
            agent=self._agent,
            user_content=user_content,
            session=session,
            run_config=self._run_config,
            a2a_inbox=A2AInbox.from_request_context(request_context),
        )
