"""Calls to the Aion model service on behalf of a turn's caller, for an extension's executor.

``model_service_access`` is read off the request while it is current - when
the turn starts - and handed over as a value, so a turn that outlives its
request still attributes its usage to the caller who started it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Optional

from aion.api.callback_attribution import callback_headers
from aion.api.model_service_client import aion_jwt_api_key, aion_model_base_url
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.settings import api_settings

__all__ = ["ModelServiceAccess", "model_service_access"]

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
