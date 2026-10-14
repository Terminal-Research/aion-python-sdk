"""What the server hands a toolkit about a request: its payload, daemon variables, model access.

Each is read off the request while it is current - before its task exists for
``Toolkit.preflight`` and the first turn, from the executor's runtime context
for a later turn - and handed over as a value, so a turn that outlives its
request still acts for the caller who started it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Optional

from a2a.types import Message

from aion.api.callback_attribution import callback_headers
from aion.api.model_service_client import aion_jwt_api_key, aion_model_base_url
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.exceptions import AionAuthenticationError
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.core.runtime.context.models import AionRuntimeContext, Event
from aion.core.settings import api_settings
from aion.core.toolkit import ModelServiceAccess, ToolkitRequest

__all__ = ["daemon_variables", "model_service_access", "toolkit_request"]

logger = logging.getLogger(__name__)


def toolkit_request(message: Optional[Message], extensions: AionRuntimeExtensions, uri: str) -> ToolkitRequest:
    """The incoming message as the toolkit serving ``uri`` reads it.

    Args:
        message: The user message of the turn.
        extensions: The request's verified extensions; the toolkit's own
            collector has validated its payload there.
        uri: The toolkit's extension URI.

    Returns:
        The message id, its text parts joined by newlines, and the toolkit's
        payload with the id and source of the event that carried it - or no
        payload when the message carries none of the toolkit's.
    """
    text = "\n".join(part.text for part in message.parts if part.HasField("text")) if message else ""
    event = extensions.get(uri)
    if not isinstance(event, Event):
        event = None
    return ToolkitRequest(
        message_id=message.message_id if message else "",
        text=text,
        payload=event.payload if event is not None else None,
        event_id=event.id if event is not None else None,
        event_source=event.source if event is not None else None,
    )


def daemon_variables(extensions: AionRuntimeExtensions) -> Mapping[str, str]:
    """The configuration variables of the request's daemon environment; empty without one."""
    daemon = extensions.get(DAEMON_EXTENSION_URI_V1)
    environment = getattr(daemon, "environment", None)
    return dict(getattr(environment, "configuration_variables", None) or {})


def model_service_access(runtime: AionRuntimeContext) -> Optional[ModelServiceAccess]:
    """Calls to the Aion model service on behalf of the request's caller, or None.

    The base URL and the token come from the deployment's platform
    credentials; the token is minted on every call, so nothing the toolkit
    keeps expires. The attribution header is the one every SDK callback of
    this request sends (``aion.api.callback_attribution.callback_headers``).

    Args:
        runtime: The request's runtime context, carrying its callback
            attribution.

    Returns:
        None when the deployment has no platform credentials, or when the
        request carries no attribution a model call can use; the toolkit
        decides whether it needs the model service at all.
    """
    if not api_settings.has_credentials:
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
