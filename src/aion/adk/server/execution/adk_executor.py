"""The ADK framework executor: runs an agent through ADK's Runner on the server's own services."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from aion.server.agent.adapters import (
    LegacyStateError,
    ExecutionConfig,
    AgentFrameworkExecutor,
)
from aion.server.agent.exceptions import ExecutionError
from aion.core.config.models import AgentConfig
from google.adk.artifacts import BaseArtifactService
from google.adk.sessions import Session, BaseSessionService

from aion.adk.server.artifacts import ArtifactServiceFactory
from aion.adk.server.artifacts.backends.a2a import A2AArtifactService
from aion.adk.server.constants import DEFAULT_USER_ID
from aion.adk.server.session import SessionServiceFactory
from aion.adk.server.transformers.a2a_to_adk import ADKTransformer
from aion.server.a2a.utils import empty_input_warning, extract_input_preview
from aion.core.exceptions import TaskFailedError

from .event_converter import ADKToA2AEventConverter
from .pause import CREDENTIAL_CALL, reply_content, waiting_call
from .result_handler import ADKExecutionResultHandler
from .runner import AionRunner, build_runner
from .stream_executor import ADKStreamExecutor, ADKStreamResult

CREDENTIALS_UNSUPPORTED = (
    "The agent asked for credentials to continue, and this server cannot take them yet."
)
"""Why a task fails when its ADK run asks for credentials (``adk_request_credential``)."""

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

AgentEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent

logger = logging.getLogger(__name__)


class ADKExecutor(AgentFrameworkExecutor):
    """The framework executor that runs an ADK agent through ADK's ``Runner``.

    The Runner is given the server's session service and artifact service
    and nothing else (``runner.build_runner``); what a turn leaves in the
    session is decided by the turn's ``ADKStreamExecutor``.
    """

    framework = "adk"

    def __init__(
            self,
            agent: Any,
            config: AgentConfig,
            session_service: Optional[BaseSessionService] = None,
            artifact_service: Optional[BaseArtifactService] = None,
            result_handler: Optional[ADKExecutionResultHandler] = None,
    ):
        self.agent = agent
        self.config = config
        self._session_service = session_service or SessionServiceFactory().create()
        self._artifact_service = artifact_service or ArtifactServiceFactory.create()
        self._result_handler = result_handler or ADKExecutionResultHandler()
        self._runners: dict[str, AionRunner] = {}

    def _runner(self, agent_id: str) -> AionRunner:
        """The Runner for the Aion agent ``agent_id``, built once and kept."""
        runner = self._runners.get(agent_id)
        if runner is None:
            runner = build_runner(
                self.agent,
                app_name=agent_id,
                session_service=self._session_service,
                artifact_service=self._artifact_service,
            )
            self._runners[agent_id] = runner
        return runner

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
        async for a2a_event in self._run(context, config, resuming=False):
            yield a2a_event

    async def _run(
            self,
            context: "RequestContext",
            config: Optional[ExecutionConfig],
            *,
            resuming: bool,
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn of the agent, and finish it the way the run ended.

        The turn is one run of ADK's Runner. A turn that resumes a task
        paused on a long-running tool call answers that call: the user's
        message becomes its function response, and the Runner resumes the
        invocation the call belongs to. Any other turn is the user's message
        in a new invocation, answered by the agent the Runner picks: the
        root agent, or the sub-agent that spoke last and may hand the
        conversation back (``transfer_to_agent``).
        """
        task_id = context.task_id or str(uuid.uuid4())
        context_id = ADKTransformer.to_session_id(config) or str(uuid.uuid4())
        converter = ADKToA2AEventConverter(task_id=task_id, context_id=context_id)
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

            scope = config.require_state_scope()
            session = await self._get_or_create_session(config, context_id)
            waiting = waiting_call(session.events) if resuming else None
            if waiting is not None:
                new_message = reply_content(waiting, context.message)
                invocation_id = waiting.invocation_id or None
                logger.info(
                    f"Answering the paused call {waiting.name} of {waiting.author}: "
                    f"context_id={context_id}"
                )
            else:
                new_message = ADKTransformer.transform_context(context)
                invocation_id = None

            stream_exec = ADKStreamExecutor(self._runner(scope.agent_id), converter)
            async for a2a_event in stream_exec.execute(
                    user_id=scope.state_owner,
                    session_id=session.id,
                    new_message=new_message,
                    invocation_id=invocation_id,
            ):
                yield a2a_event

            async for a2a_event in self._finalize(
                    stream_exec.result, converter, stream_exec.session or session,
                    context, task_id, context_id,
            ):
                yield a2a_event

            logger.info(f"ADK stream completed: context_id={context_id}, duration={time.monotonic() - start:.3f}s")

        except Exception as e:
            # The request executor decides what the task says and logs the
            # failure once; this adds only how long the run lasted.
            logger.debug(f"ADK stream failed after {time.monotonic() - start:.3f}s: {e}")
            raise ExecutionError(f"Failed to stream agent: {e}") from e
        finally:
            self._forget_turn_artifacts(config, context_id)

    def _forget_turn_artifacts(self, config: Optional[ExecutionConfig], session_id: str) -> None:
        """Let the artifact service drop the bytes this turn's artifacts held in memory.

        The turn has ended however it ended - the server closes the stream
        once it has handed every event on - so its task holds what reached it.
        """
        service = self._artifact_service
        if not isinstance(service, A2AArtifactService) or config is None:
            return
        if config.task_id is None or config.state_scope is None:
            return
        service.forget_turn(
            app_name=config.state_scope.agent_id,
            user_id=config.state_scope.state_owner,
            session_id=session_id,
            task_id=config.task_id,
        )

    async def _finalize(
            self,
            stream_result: ADKStreamResult,
            converter: ADKToA2AEventConverter,
            session: Any,
            context: "RequestContext",
            task_id: str,
            context_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """Close a streaming section the run left open, then emit result events and the turn's end.

        The end is ``INPUT_REQUIRED`` when the run paused on a long-running
        tool call, and ``COMPLETED`` otherwise. A run whose events reported
        an error (``Event.error_code``) fails the task with that error, as
        ADK's own A2A executor ends such a run: a blocked or cut-off model
        response is not an answer. A request for credentials fails the task
        too: the server cannot take credentials yet.

        Raises:
            TaskFailedError: An event of the run reported an error, or the
                run paused on ``adk_request_credential``.
        """
        if stream_result.error is not None:
            raise TaskFailedError(stream_result.error.reason)

        paused_on = stream_result.paused_on
        if paused_on is not None and paused_on.name == CREDENTIAL_CALL:
            raise TaskFailedError(CREDENTIALS_UNSUPPORTED)

        for a2a_event in await converter.finalize_stream():
            yield a2a_event

        for a2a_event in self._result_handler.handle(
                stream_result, converter, session, context, task_id, context_id
        ):
            yield a2a_event

        if paused_on is not None:
            yield converter.generate_pause(paused_on)
        else:
            yield converter.generate_complete()

    async def delete_state(self, config: ExecutionConfig) -> None:
        """Delete the context's ADK session and the artifacts saved under it.

        Keyed as every execution of the context was: the agent as the app,
        the scope's state owner as the user, the context as the session.
        Artifacts kept in the task tables go with the context's tasks; this
        removes the ones the artifact service still holds in memory.
        """
        scope = config.require_state_scope()
        session_id = ADKTransformer.to_session_id(config)
        keys = await self._artifact_service.list_artifact_keys(
            app_name=scope.agent_id, user_id=scope.state_owner, session_id=session_id
        )
        for filename in keys:
            await self._artifact_service.delete_artifact(
                app_name=scope.agent_id,
                user_id=scope.state_owner,
                filename=filename,
                session_id=session_id,
            )
        session = await self._session_service.get_session(
            app_name=scope.agent_id, user_id=scope.state_owner, session_id=session_id
        )
        if session is not None:
            await self._session_service.delete_session(
                app_name=scope.agent_id, user_id=scope.state_owner, session_id=session_id
            )

    async def discard_undelivered(
            self,
            config: ExecutionConfig,
            event: AgentEvent,
    ) -> None:
        """Remove an artifact that never reached the task from the artifact service.

        The converter loads every artifact it emits from the artifact service,
        which keeps the bytes in memory until the turn ends, and a user-scoped
        artifact for a while after it. An artifact the server could not store
        must not be readable from there by a later turn, since the client
        never received it.

        ``delete_artifact`` is the only removal the ADK interface offers, and
        it takes every version of the name the service holds. In
        ``A2AArtifactService`` that is the in-memory copy alone: versions that
        reached the task are still read back from the task tables, and the
        next save numbers its version after them. Without a database those
        versions are gone with it, as they would be once their time in memory
        ran out.
        """
        if not isinstance(event, TaskArtifactUpdateEvent) or not event.artifact.name:
            return
        scope = config.require_state_scope()
        await self._artifact_service.delete_artifact(
            app_name=scope.agent_id,
            user_id=scope.state_owner,
            filename=event.artifact.name,
            session_id=ADKTransformer.to_session_id(config),
        )
        logger.info(
            "Removed artifact %r from the artifact service: it never reached the task",
            event.artifact.name,
        )

    async def resume(
            self,
            context: "RequestContext",
            config: ExecutionConfig,
    ) -> AsyncIterator[AgentEvent]:
        """Resume ADK agent execution with new user input.

        A task waits for input when its run paused on a long-running tool
        call; the message answers that call, and the invocation it belongs
        to runs on (``_run``). A task left waiting without one - an outbox Task set
        the state - goes on with the message as an ordinary turn in the same
        session.

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

            async for event in self._run(context, config, resuming=True):
                yield event

        except Exception as e:
            logger.debug(f"Failed to resume ADK execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    async def _get_or_create_session(self, config: ExecutionConfig, session_id: str) -> Session:
        """Get the caller's session for this context, or create it.

        Keyed by ``app_name`` = the Aion agent id and ``user_id`` = the
        conversation's state owner (``StateScope.state_owner``: the caller's
        ``owner_scope``, or the gateway conversation's), with the A2A
        ``context_id`` as ``session_id``: two users - or two agents on one
        database - that present the same ``context_id`` get two sessions,
        while the participants of one gateway conversation share one.

        Raises:
            LegacyStateError: No such session exists, but one saved before
                sessions were keyed by agent and owner does.
        """
        scope = config.require_state_scope()
        session = await self._session_service.get_session(
            app_name=scope.agent_id,
            user_id=scope.state_owner,
            session_id=session_id,
        )
        if session:
            logger.debug(f"Session resumed: {session_id}")
            return session

        await self._refuse_legacy_session(session_id)
        logger.info(f"Session created: {session_id}")
        return await self._session_service.create_session(
            app_name=scope.agent_id,
            user_id=scope.state_owner,
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
