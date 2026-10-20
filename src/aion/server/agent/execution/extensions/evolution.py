"""Behavior evolution as an executable extension: the SDK's thin wrapper around the evolution toolkit.

The extension (``BEHAVIOR_EVOLUTION_EXTENSION_URI_V1``) evolves an agent's
repository: the evolution toolkit (``aion-toolkit-behavior-evolution``, the
package ``aion.toolkits.behavior_evolution``) plans and implements a change
with Codex and delivers it on an evolution branch. The SDK holds no evolution
logic, only what connects the toolkit to the server:

* the options ``aion.yaml`` gives the extension (``EvolutionOptions``):
  ``callers`` - who may start an evolution - and ``provider`` - where the
  executor's model calls go. The executor is created only when the toolkit
  is installed, the provider has what it needs and the toolkit can run here
  (its settings, ``codex``, ``git``); otherwise the server does not start;
* what a new task is refused with, before the run starts - a final
  ``REJECTED`` status with the code: a caller kind the options do not list,
  or one the model service cannot serve (``caller_not_allowed``), a
  directive that is malformed (``invalid_payload``) or that the toolkit
  refuses, and a context that already has an unfinished evolution task
  (``context_busy``);
* the run's context: the model access the provider gives this caller (the
  Aion model service on the caller's behalf, a fresh token for every call; a
  custom endpoint with its key, when it has one; none for the operator's own
  Codex login), the context's generation, the deployment's secret values,
  which the toolkit masks in what it reports, and the lookup of the
  context's ``evolution-spec`` artifact, the plan a run may start from;
* the version number of every artifact the run writes in the context
  (``metadata["version"]``).

What the wrapper reads of the context - its generation, its unfinished
evolution task, its artifact versions - comes from the turn's history
(``aion.core.agent.conversation``), which the server installs for every turn.

The toolkit is imported lazily: a server that does not enable the extension
never imports it. The toolkit yields the A2A events of a run itself; the
server hands them on the standard way, and writes the final status of a run
it cancels or stops.

Provider variables:

- ``aion``: ``AION_CLIENT_ID`` and ``AION_CLIENT_SECRET``;
- ``custom``: ``EVOLUTION_MODEL_BASE_URL``, an OpenAI-Responses-compatible
  endpoint, and ``EVOLUTION_MODEL_API_KEY``, its bearer key, when it needs
  one; both read for every run, so a rotated key reaches the next one;
- ``local_session``: none here; the toolkit runs Codex under the operator's
  login (``CODEX_HOME``).
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from types import ModuleType
from typing import TYPE_CHECKING, Any, Optional

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from pydantic import BaseModel, ConfigDict, Field

from aion.core.a2a import codes
from aion.core.agent import conversation
from aion.core.constants.a2a import BEHAVIOR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1
from aion.core.exceptions import ConfigurationError, ExtensionRejection
from aion.core.runtime.context.extensions import ExtensionActivationError, MessagesCollector
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.settings import api_settings
from aion.server.a2a.utils import routed_extension_of
from aion.server.agent.adapters import ExecutionConfig
from aion.server.auth import AuthenticatedCaller, CredentialKind, verified_caller

from .executable import ExecutableExtensionDescriptor, ExtensionExecutor, parse_options
from .secret_values import deployment_secret_values
from .model_service import model_service_access

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = [
    "EVOLUTION_EXTENSION",
    "MODEL_API_KEY_VARIABLE",
    "MODEL_BASE_URL_VARIABLE",
    "TOOLKIT_MODULE",
    "Caller",
    "EvolutionExecutor",
    "EvolutionOptions",
    "Provider",
]

logger = logging.getLogger(__name__)

URI = BEHAVIOR_EVOLUTION_EXTENSION_URI_V1

TOOLKIT_MODULE = "aion.toolkits.behavior_evolution.plugin"
"""The module of the evolution toolkit the wrapper drives."""

TOOLKIT_PACKAGE = "aion-toolkit-behavior-evolution"
"""The distribution that installs the toolkit."""

MODEL_BASE_URL_VARIABLE = "EVOLUTION_MODEL_BASE_URL"
"""The endpoint of the ``custom`` provider."""

MODEL_API_KEY_VARIABLE = "EVOLUTION_MODEL_API_KEY"
"""The bearer key of the ``custom`` provider's endpoint; unset for an endpoint that takes none."""

NO_MODEL_SERVICE = "The model service cannot be called for this caller, so the evolution cannot run on its behalf."
"""Why a request is refused when the ``aion`` provider has no model access for its caller."""


class Caller(StrEnum):
    """A kind of caller ``callers`` may let start an evolution."""

    PLATFORM = "platform"
    """A call the Aion platform dispatched, with an invocation token (``a2a_invocation``)."""

    ANONYMOUS_SESSION = "anonymous_session"
    """A direct client with an anonymous session token Aion issued to it (``anonymous_session``)."""


class Provider(StrEnum):
    """Where the executor's model calls go."""

    AION = "aion"
    """The Aion model service, on the caller's behalf."""

    CUSTOM = "custom"
    """An OpenAI-Responses-compatible endpoint (``EVOLUTION_MODEL_BASE_URL``)."""

    LOCAL_SESSION = "local_session"
    """The operator's own Codex login on this host."""


_CREDENTIAL_CALLERS = {
    CredentialKind.INVOCATION: Caller.PLATFORM,
    CredentialKind.SESSION: Caller.ANONYMOUS_SESSION,
}


class EvolutionOptions(BaseModel):
    """The options ``aion.yaml`` gives the evolution extension.

    Attributes:
        callers: The kinds of caller that may start an evolution; any other
            caller is refused with ``caller_not_allowed``. An application's
            own user (``trust_application_users``) is never one of them.
        provider: Where the executor's model calls go.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    callers: tuple[Caller, ...] = Field(default=(Caller.PLATFORM,), min_length=1)
    provider: Provider = Provider.AION


class EvolutionExecutor(ExtensionExecutor):
    """The executor of the evolution extension: the toolkit, prepared for this deployment and caller.

    Raises:
        ConfigurationError: The options are not valid, the toolkit is not
            installed, the provider lacks what it needs, or the toolkit
            cannot run here.
    """

    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(options)
        self.options = parse_options(EvolutionOptions, URI, options)
        try:
            toolkit = _toolkit_module()
        except ModuleNotFoundError as error:
            if not _is_toolkit_module(error.name):
                raise
            raise _cannot_run(
                f"the behavior-evolution toolkit is not installed (pip install {TOOLKIT_PACKAGE})"
            ) from None
        provider = self.options.provider.value
        problem = _provider_problem(self.options.provider) or toolkit.availability(provider=provider)
        if problem is not None:
            raise _cannot_run(problem)
        self._toolkit = toolkit.EvolutionToolkit(provider=provider)
        self._directives = MessagesCollector(
            toolkit.EvolutionDirectiveEventPayload, invalid_payload_code=codes.INVALID_PAYLOAD
        )

    async def stream(
            self, context: "RequestContext", config: ExecutionConfig
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run one evolution with the model access the provider gives this caller.

        Raises:
            ExtensionRejection: The request is refused before the run starts
                (see the module's docstring).
        """
        history = conversation.history()
        request, model = await self._admit(context, config, history)
        toolkit = _toolkit_module()
        run = toolkit.EvolutionRun(
            task_id=config.task_id,
            context_id=config.context_id,
            turn_id=context.message.message_id,
            context_generation=await history.generation(),
            model=model,
            secret_values=tuple(deployment_secret_values()),
            find_artifact=history.find_artifact,
        )
        async for event in self._toolkit.start(request, run):
            if isinstance(event, TaskArtifactUpdateEvent):
                await history.stamp_artifact_version(event)
            yield event

    async def resume(
            self, context: "RequestContext", config: ExecutionConfig
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Hand a further message to the toolkit, which refuses it: an evolution never waits for input."""
        toolkit = _toolkit_module()
        run = toolkit.EvolutionRun(
            task_id=config.task_id, context_id=config.context_id, turn_id=context.message.message_id
        )
        async for event in self._toolkit.resume(self._evolution_request(context), run):
            yield event

    async def cancel(self, config: ExecutionConfig, *, within: Optional[float] = None) -> None:
        """Stop the run of ``config.task_id`` in this process; it rescues its work within ``within`` seconds."""
        await self._toolkit.cancel(config.task_id, within=within)

    # -- internals ------------------------------------------------------------

    async def _admit(
            self, context: "RequestContext", config: ExecutionConfig, history: conversation.ConversationHistory
    ) -> tuple[Any, Any]:
        """The toolkit's request and the run's model access; refuses what may not run.

        The caller comes first, so a caller the options do not allow learns
        nothing of the context; the context's unfinished task comes last.

        Raises:
            ExtensionRejection: ``caller_not_allowed``, ``invalid_payload``,
                the toolkit's own refusal (``unsupported_directive``,
                ``forbidden_target``) or ``context_busy``.
        """
        caller = _caller_kind(verified_caller(context.call_context))
        if caller not in self.options.callers:
            allowed = ", ".join(kind.value for kind in self.options.callers)
            logger.info("Refused an evolution request from a caller this agent does not allow (%s)", caller or "other")
            raise ExtensionRejection(
                codes.CALLER_NOT_ALLOWED,
                (
                    f"This kind of caller ({caller.value if caller else 'neither platform nor anonymous session'}) "
                    f"may not start an evolution on this agent: its evolution extension allows {allowed}."
                ),
                retryable=codes.RETRYABLE[codes.CALLER_NOT_ALLOWED],
            )
        model = await self._model_endpoint()
        request = self._evolution_request(context)
        await self._toolkit.preflight(request)
        blocking = next(
            (
                task.id
                for task in await history.tasks(unfinished=True)
                if routed_extension_of(task) == URI and task.id != config.task_id
            ),
            None,
        )
        if blocking is not None:
            raise ExtensionRejection(
                codes.CONTEXT_BUSY,
                (
                    f"Context {config.context_id} already has an unfinished evolution task ({blocking}); "
                    "wait for it to end or cancel it, then send the request again."
                ),
                retryable=codes.RETRYABLE[codes.CONTEXT_BUSY],
            )
        return request, model

    def _evolution_request(self, context: "RequestContext") -> Any:
        """The toolkit's view of the request: the message and its directive.

        Raises:
            ExtensionRejection: ``invalid_payload`` - a directive part the
                directive model refuses.
        """
        try:
            event = self._directives.collect(URI, context)
        except ExtensionActivationError as error:
            raise ExtensionRejection(
                codes.INVALID_PAYLOAD,
                error.reason or "The evolution directive is not valid.",
                retryable=codes.RETRYABLE[codes.INVALID_PAYLOAD],
            ) from error
        return _toolkit_module().EvolutionRequest(
            message_id=context.message.message_id,
            context_id=context.context_id,
            text=context.get_user_input(),
            directive=event.payload if event is not None else None,
        )

    async def _model_endpoint(self) -> Any:
        """How the executor reaches its model for this run; None for the operator's own login.

        Raises:
            ExtensionRejection: ``caller_not_allowed`` - the ``aion``
                provider cannot call the model service for this caller.
        """
        toolkit = _toolkit_module()
        provider = self.options.provider
        if provider is Provider.LOCAL_SESSION:
            return None
        if provider is Provider.CUSTOM:
            api_key = None
            if os.environ.get(MODEL_API_KEY_VARIABLE, "").strip():

                async def api_key() -> str:
                    # Read at the moment of the call: a rotated key reaches the next one.
                    return os.environ.get(MODEL_API_KEY_VARIABLE, "").strip()

            return toolkit.ModelEndpoint(base_url=os.environ[MODEL_BASE_URL_VARIABLE].strip(), secret=api_key)
        access = model_service_access(await AionRuntimeContextRegistry.aget_current_context())
        if access is None:
            raise ExtensionRejection(
                codes.CALLER_NOT_ALLOWED,
                NO_MODEL_SERVICE,
                retryable=codes.RETRYABLE[codes.CALLER_NOT_ALLOWED],
            )
        return toolkit.ModelEndpoint(base_url=access.base_url, secret=access.token, attribution=access.attribution)


def _toolkit_module() -> ModuleType:
    """The evolution toolkit's module; imported on first use.

    Raises:
        ModuleNotFoundError: The toolkit is not installed, or a module it
            needs is missing.
    """
    return importlib.import_module(TOOLKIT_MODULE)


def _is_toolkit_module(name: Optional[str]) -> bool:
    """Whether a missing module is the toolkit itself rather than something the installed toolkit needs."""
    return bool(name) and (TOOLKIT_MODULE == name or TOOLKIT_MODULE.startswith(f"{name}."))


def _cannot_run(reason: str) -> ConfigurationError:
    """The configuration error of an evolution extension that cannot run on this server."""
    return ConfigurationError(f"the extension {URI} cannot run on this server: {reason}")


def _provider_problem(provider: Provider) -> Optional[str]:
    """What the provider needs and the deployment lacks, or None."""
    if provider is Provider.AION and not api_settings.has_credentials:
        return (
            "the evolution's provider 'aion' calls the Aion model service with the deployment's "
            "credentials, and AION_CLIENT_ID and AION_CLIENT_SECRET are not set"
        )
    if provider is Provider.CUSTOM and not os.environ.get(MODEL_BASE_URL_VARIABLE, "").strip():
        return f"the evolution's provider 'custom' needs {MODEL_BASE_URL_VARIABLE}, which is not set"
    return None


def _caller_kind(caller: Optional[AuthenticatedCaller]) -> Optional[Caller]:
    """The kind of a verified caller, as ``callers`` names it; None for any other caller."""
    if caller is None:
        return None
    return _CREDENTIAL_CALLERS.get(caller.credential)


EVOLUTION_EXTENSION = ExecutableExtensionDescriptor(
    uri=URI,
    requires=(DAEMON_EXTENSION_URI_V1,),
    description=(
        "Evolves an agent's repository: plans and implements a change with Codex "
        "and delivers it on an evolution branch."
    ),
    active=False,
    executor=EvolutionExecutor,
)
"""The evolution extension: enabled by ``aion.yaml``, needs the daemon extension."""
