"""The generic handler that serves one toolkit plugin.

``ToolkitTaskHandler`` is the ``ExtensionTaskHandler`` of every toolkit: one
instance per available toolkit, built by discovery. It does what the toolkit
protocol leaves to the server:

* **admission** - before a task exists, the caller check of an
  ``invocation_only`` toolkit and the toolkit's ``preflight``; a refusal is a
  JSON-RPC error whose ``data`` carries ``code`` and ``retryable``, and no
  task is created;
* **turns** - ``start`` or ``resume`` with a ``ToolkitContext``, every value
  they yield translated by a ``TurnTranslator``; the turn is events, then
  exactly one result, and a turn that breaks that, yields something else or
  raises ends ``FAILED`` with ``internal_error``;
* **cancellation** - ``Toolkit.cancel`` and a bounded wait for the running
  turn to end, after which the executor writes ``CANCELED``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.utils.errors import InternalError, InvalidParamsError
from google.protobuf import struct_pb2

from aion.core.exceptions import AionAuthenticationError, InvalidPrincipalError, ToolkitRejection
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.settings import api_settings
from aion.core.toolkit import (
    CANCEL,
    ContextArtifact,
    ModelServiceAccess,
    PreflightContext,
    Toolkit,
    ToolkitArtifactEvent,
    ToolkitContext,
    ToolkitMessageEvent,
    ToolkitRequest,
    ToolkitResult,
    ToolkitStatusEvent,
    codes,
)
from aion.core.toolkit.plugin import ArtifactFinder
from aion.core.utils.protobuf import proto_to_dict
from aion.server.auth import CredentialKind, verified_caller

from ...context.attribution import callback_attribution
from .secret_values import SecretMasker, deployment_secret_values
from .services import daemon_variables, model_service_access, toolkit_request
from .translation import ToolkitProtocolError, TurnTranslator

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from aion.core.runtime.context.extensions import AionRuntimeExtensions

__all__ = [
    "CANCEL_GRACE_SECONDS",
    "PREFLIGHT_CRASH_MESSAGE",
    "ToolkitAdmission",
    "ToolkitTaskHandler",
    "refusal_data",
]

logger = logging.getLogger(__name__)

CANCEL_GRACE_SECONDS = 15.0
"""How long a cancel waits for the running turn to end before the task is cancelled without it."""

PREFLIGHT_CRASH_MESSAGE = "The toolkit failed while checking the request; the server log has the details."
"""The JSON-RPC error message of a ``preflight`` that raised something other than a refusal."""

async def _no_artifact(_artifact_id: str) -> Optional[ContextArtifact]:
    return None


def refusal_data(code: str, retryable: Optional[bool] = None) -> dict[str, str]:
    """The ``data`` of a JSON-RPC error that refuses a request with ``code``.

    a2a-sdk sends ``data`` as the metadata of a ``google.rpc.ErrorInfo``,
    whose values are strings, so ``retryable`` travels as ``"true"`` or
    ``"false"``.

    Args:
        code: The refusal's code: a toolkit's own, or one of
            ``aion.core.toolkit.codes``.
        retryable: Whether repeating the request can succeed; by default the
            fixed answer for a code the server reserves.
    """
    if retryable is None:
        retryable = codes.RETRYABLE[code]
    return {"code": code, "retryable": "true" if retryable else "false"}


@dataclass(frozen=True)
class ToolkitAdmission:
    """What admission read off a request that starts a new task of a toolkit.

    Handed to the first turn, so the toolkit starts with the request
    ``preflight`` checked.

    Attributes:
        request: The message as the toolkit's payload models read it.
        daemon_variables: Configuration variables of the request's daemon
            environment.
        model_service: Model service access on the caller's behalf, or None.
    """

    request: ToolkitRequest
    daemon_variables: Mapping[str, str]
    model_service: Optional[ModelServiceAccess]


class ToolkitTaskHandler:
    """Serves the tasks of one toolkit: admission, turns and cancellation.

    Args:
        toolkit: The toolkit plugin.
        artifact_finder: Looks up the latest version of an artifact in a
            task's context, for ``ToolkitContext.find_artifact``; finds
            nothing by default.
        secret_values: Returns the deployment's secret values; read at the
            start of every turn.
    """

    def __init__(
            self,
            toolkit: Toolkit,
            *,
            artifact_finder: ArtifactFinder = _no_artifact,
            secret_values: Callable[[], Iterable[str]] = deployment_secret_values,
    ) -> None:
        self.toolkit = toolkit
        self.descriptor = toolkit.descriptor
        self.uri = self.descriptor.uri
        self._artifact_finder = artifact_finder
        self._secret_values = secret_values
        # The turn each task is running in this process, set once it ends.
        self._turns: dict[str, asyncio.Event] = {}

    # -- admission ----------------------------------------------------------

    async def admit(
            self,
            message: Message,
            extensions: "AionRuntimeExtensions",
            call_context: "ServerCallContext",
    ) -> ToolkitAdmission:
        """Check a request that starts a new task of this toolkit, before the task exists.

        The caller check comes first, then the toolkit's ``preflight``.

        Raises:
            InvalidParamsError: The caller may not start this toolkit
                (``caller_not_allowed``), or ``preflight`` refused the request
                with a ``ToolkitRejection`` (its own code and ``retryable``).
            InternalError: ``preflight`` raised anything else
                (``internal_error``); the exception is logged.
        """
        self._check_caller(call_context)
        runtime = _request_runtime(extensions, call_context)
        admission = ToolkitAdmission(
            request=toolkit_request(message, extensions, self.uri),
            daemon_variables=daemon_variables(extensions),
            model_service=model_service_access(runtime) if runtime is not None else None,
        )
        preflight_context = PreflightContext(
            context_id=message.context_id,
            daemon_variables=admission.daemon_variables,
            model_service=admission.model_service,
        )
        try:
            await self.toolkit.preflight(admission.request, preflight_context)
        except ToolkitRejection as rejection:
            logger.info("Toolkit %s refused a request [%s]: %s", self.uri, rejection.code, rejection.message)
            raise InvalidParamsError(
                message=rejection.message,
                data=refusal_data(rejection.code, rejection.retryable),
            ) from rejection
        except Exception:
            logger.exception("Toolkit %s failed while checking a request", self.uri)
            raise InternalError(
                message=PREFLIGHT_CRASH_MESSAGE,
                data=refusal_data(codes.INTERNAL_ERROR),
            ) from None
        return admission

    def _check_caller(self, call_context: "ServerCallContext") -> None:
        """Refuse a caller an ``invocation_only`` toolkit does not serve.

        On a server with ``AION_CLIENT_ID`` such a toolkit serves only calls
        the platform dispatched, with an invocation token. The rule follows
        the setting rather than a successful registration with the platform:
        a platform unreachable at startup must not open the toolkit to
        everyone. Without ``AION_CLIENT_ID`` - local development - there is
        no restriction.

        Raises:
            InvalidParamsError: ``caller_not_allowed``.
        """
        if not self.descriptor.invocation_only or not api_settings.client_id:
            return
        caller = verified_caller(call_context)
        if caller is not None and caller.credential is CredentialKind.INVOCATION:
            return
        logger.info("Refused a caller without an invocation token for toolkit %s", self.uri)
        raise InvalidParamsError(
            message=f"Only calls the Aion platform dispatches may start {self.uri} on this agent.",
            data=refusal_data(codes.CALLER_NOT_ALLOWED),
        )

    # -- turns --------------------------------------------------------------

    async def stream(
            self, context: "RequestContext", admission: ToolkitAdmission
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the first turn of a new task with the admitted request."""
        async for event in self._turn(
            context,
            self.toolkit.start,
            admission.request,
            admission.daemon_variables,
            admission.model_service,
            task_metadata={},
        ):
            yield event

    async def resume(
            self, context: "RequestContext"
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Run the next turn of a task, with the toolkit's object as the task holds it."""
        runtime = await AionRuntimeContextRegistry.aget_current_context() or AionRuntimeContext()
        task = context.current_task
        async for event in self._turn(
            context,
            self.toolkit.resume,
            toolkit_request(context.message, runtime.extensions, self.uri),
            daemon_variables(runtime.extensions),
            model_service_access(runtime),
            task_metadata=_stored_object(task, self.uri),
        ):
            yield event

    async def _turn(
            self,
            context: "RequestContext",
            run: Callable[[ToolkitRequest, ToolkitContext], AsyncIterator[Any]],
            request: ToolkitRequest,
            variables: Mapping[str, str],
            model_service: Optional[ModelServiceAccess],
            *,
            task_metadata: Mapping[str, Any],
    ) -> AsyncIterator[TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Drive one turn of the toolkit and translate it; the turn closes on exactly one final status."""
        task = context.current_task
        masker = SecretMasker([*self._secret_values(), *deployment_secret_values(variables)])
        toolkit_context = ToolkitContext(
            task_id=task.id,
            context_id=task.context_id,
            task_metadata=task_metadata,
            daemon_variables=variables,
            model_service=_masking_tokens(model_service, masker),
            secret_values=masker.values,
            artifact_finder=self._artifact_finder,
        )
        translator = TurnTranslator(
            uri=self.uri,
            task_id=task.id,
            context_id=task.context_id,
            turn_id=request.message_id,
            task_metadata=task_metadata,
            masker=masker,
        )
        finished = asyncio.Event()
        self._turns[task.id] = finished
        outputs: Any = None
        try:
            try:
                outputs = run(request, toolkit_context)
                result: Optional[ToolkitResult] = None
                async for value in outputs:
                    if result is not None:
                        raise ToolkitProtocolError("the toolkit yielded a value after its result")
                    if isinstance(value, ToolkitResult):
                        result = value
                    elif isinstance(value, (ToolkitMessageEvent, ToolkitArtifactEvent, ToolkitStatusEvent)):
                        for event in await translator.event(value):
                            yield event
                    else:
                        raise ToolkitProtocolError(
                            f"the toolkit yielded {type(value).__name__}, which is not a protocol value"
                        )
                if result is None:
                    raise ToolkitProtocolError("the toolkit's turn ended without a result")
                closing = await translator.result(result)
            except ToolkitProtocolError as error:
                logger.error("Toolkit %s broke the protocol in task %s: %s", self.uri, task.id, error)
                closing = translator.failure()
            except Exception:
                logger.exception("Toolkit %s failed in task %s", self.uri, task.id)
                closing = translator.failure()
            finally:
                await _close(outputs)
            for event in closing:
                yield event
        finally:
            finished.set()
            if self._turns.get(task.id) is finished:
                del self._turns[task.id]

    # -- cancellation -------------------------------------------------------

    async def cancel(self, context: "RequestContext", event_queue: "EventQueue") -> Optional[Message]:
        """Ask the toolkit to stop, and give the running turn a bounded time to end.

        The turn may still yield artifacts and a ``canceled`` result in that
        time; they reach the task as any turn's would. The executor writes
        ``CANCELED`` once this returns, whether the turn ended or not.

        Returns:
            None: the executor's ``CANCELED`` carries no message of its own.
        """
        task_id = context.task_id or (context.current_task.id if context.current_task else "")
        try:
            await self.toolkit.cancel(task_id, CANCEL)
        except Exception:
            logger.exception("Toolkit %s failed to cancel task %s", self.uri, task_id)
        finished = self._turns.get(task_id)
        if finished is None:
            return None
        try:
            await asyncio.wait_for(finished.wait(), CANCEL_GRACE_SECONDS)
        except TimeoutError:
            logger.warning(
                "Toolkit %s did not end the turn of task %s within %.0fs of the cancel; "
                "the task is cancelled without it",
                self.uri,
                task_id,
                CANCEL_GRACE_SECONDS,
            )
        return None


def _request_runtime(
        extensions: "AionRuntimeExtensions", call_context: "ServerCallContext"
) -> Optional[AionRuntimeContext]:
    """The runtime context the executor will build for this request, as far as callbacks need it.

    None when the request carries no callback attribution the server
    accepts; the executor refuses such a request later anyway.
    """
    try:
        attribution = callback_attribution(call_context, AionRuntimeContext(extensions=extensions))
    except (AionAuthenticationError, InvalidPrincipalError) as error:
        logger.debug("No callback attribution for this request: %s", error)
        return None
    return AionRuntimeContext(extensions=extensions, callback_attribution=attribution)


def _stored_object(task: Task, uri: str) -> dict[str, Any]:
    """The toolkit's object as the task holds it; empty when there is none."""
    if not task.HasField("metadata") or uri not in task.metadata:
        return {}
    value = task.metadata[uri]
    return proto_to_dict(value) if isinstance(value, struct_pb2.Struct) else {}


def _masking_tokens(
        access: Optional[ModelServiceAccess], masker: SecretMasker
) -> Optional[ModelServiceAccess]:
    """``access`` whose every minted token is masked in the turn from the moment it exists."""
    if access is None:
        return None
    mint = access.token

    async def token() -> str:
        value = await mint()
        masker.add(value)
        return value

    return dataclasses.replace(access, token=token)


async def _close(outputs: Any) -> None:
    """Close the toolkit's stream, so a turn torn down early stops the toolkit's work."""
    aclose = getattr(outputs, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.exception("Closing a toolkit's stream failed")
