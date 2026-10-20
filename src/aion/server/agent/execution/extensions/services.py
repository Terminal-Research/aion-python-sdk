"""What the server offers an extension's executor: its context services, and the model service for the caller.

``ExtensionServices`` reads a context: its generation, its unfinished task of
an extension, and the versions of an artifact among its tasks. The executor
gets it when it is created.

``model_service_access`` is read off the request while it is current - when
the turn starts - and handed over as a value, so a turn that outlives its
request still attributes its usage to the caller who started it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope

from aion.api.callback_attribution import callback_headers
from aion.api.model_service_client import aion_jwt_api_key, aion_model_base_url
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.settings import api_settings
from aion.server.tasks.admission import holder_of
from aion.server.tasks.context_artifacts import ContextArtifacts
from aion.server.tasks.context_runs import ContextRuns

from .artifacts import TurnArtifacts

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = ["ExtensionServices", "ModelServiceAccess", "model_service_access"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelServiceAccess:
    """Calls to the Aion model service on the current caller's behalf.

    Attributes:
        base_url: Base URL of the model service's OpenAI-compatible API.
        token: Returns a fresh short-lived bearer token; call it for every
            model call rather than keeping one.
        attribution: The header that attributes usage to the caller, as
            ``(name, value)``; None when usage is attributed to the
            deployment itself.
    """

    base_url: str
    token: Callable[[], Awaitable[str]]
    attribution: Optional[tuple[str, str]] = None


def model_service_access(runtime: Optional[AionRuntimeContext]) -> Optional[ModelServiceAccess]:
    """Calls to the Aion model service on behalf of the request's caller, or None.

    The base URL and the token come from the deployment's platform
    credentials; the token is minted on every call, so nothing an executor
    keeps expires. The attribution header is the one every SDK callback of
    this request sends (``aion.api.callback_attribution.callback_headers``).

    Args:
        runtime: The request's runtime context, carrying its callback
            attribution; None for a request that carries none.

    Returns:
        None when the deployment has no platform credentials, or when the
        request carries no attribution a model call can use.
    """
    if runtime is None or not api_settings.has_credentials:
        return None
    try:
        headers = callback_headers(context=runtime)
    except AionAuthenticationError as error:
        logger.debug("No model service access for this request: %s", error)
        return None
    attribution = next(iter(headers.items()), None)
    return ModelServiceAccess(
        base_url=aion_model_base_url(),
        token=aion_jwt_api_key,
        attribution=attribution,
    )


class ExtensionServices:
    """The server's context services, as an extension's executor reads them.

    Args:
        context_artifacts: Reads the versions of an artifact among the tasks
            of a context; without it nothing is numbered or found.
        context_runs: Reads a context's generation and its unfinished task of
            an extension; without it there is neither.
        owner_resolver: Resolves a call's owner, as the task store does; it
            gives the context holder a caller was admitted as.
    """

    def __init__(
            self,
            *,
            context_artifacts: Optional[ContextArtifacts] = None,
            context_runs: Optional[ContextRuns] = None,
            owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        self._context_artifacts = context_artifacts
        self._context_runs = context_runs
        self._owner_resolver = owner_resolver

    async def context_generation(self, context_id: str) -> Optional[str]:
        """Opaque marker of this use of the context id; None when the server does not track it.

        It changes when a deleted context's id is taken again, so state an
        executor keeps outside the server (a branch, a file) can tell an old
        holder of the same id from the current one.
        """
        if self._context_runs is None:
            return None
        return await self._context_runs.generation(context_id)

    async def unfinished_task(self, context_id: str, uri: str) -> Optional[str]:
        """The oldest task of the context routed to ``uri`` that has no outcome yet, or None.

        Any owner's: the participants of a shared context share an
        extension's state of the context too. The answer comes from the
        store, so it holds across the servers of the agent; nothing locks the
        context between this read and a later write.
        """
        if self._context_runs is None:
            return None
        return await self._context_runs.unfinished_task(context_id, uri)

    def artifacts(self, context: "RequestContext") -> Optional[TurnArtifacts]:
        """The artifact versions of the request's task, within the context holder its caller was admitted as.

        None without the context artifacts service, or before the request has
        a task.
        """
        task = context.current_task
        if self._context_artifacts is None or task is None:
            return None
        return TurnArtifacts(
            self._context_artifacts,
            context_id=task.context_id,
            task_id=task.id,
            holder=holder_of(context.call_context, self._owner_resolver),
        )
