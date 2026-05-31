"""OpenAI-compatible model API configuration helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx
from aion.api.control_plane import (
    AION_PRINCIPAL_SELECTOR_HEADER,
    PrincipalSelector,
    PrincipalSelectorKind,
)
from aion.api.exceptions import AionAuthenticationError
from aion.api.http.jwt_manager import (
    AionJWTManager,
    AionRefreshingJWTManager,
    aion_jwt_manager,
)
from aion.core.runtime.context import get_aion_runtime_context
from aion.core.settings import api_settings

PrincipalSelectorProvider = Callable[[], str | None]
ModelApiKeyProvider = Callable[[], str]

logger = logging.getLogger(__name__)

_AION_OPENAI_API_KEY_PLACEHOLDER = "aion-runtime-token"


@dataclass(frozen=True)
class AionModelClientConfig:
    """Configuration for OpenAI-compatible model clients."""

    base_url: str
    api_key: str | ModelApiKeyProvider
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def openai_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by OpenAI-compatible clients."""
        kwargs: dict[str, object] = {
            "base_url": self.base_url,
            "api_key": self.api_key,
        }
        headers = dict(self.default_headers)
        headers.setdefault("Accept", "application/json, text/event-stream")
        if headers:
            kwargs["default_headers"] = headers
        return kwargs

    def litellm_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by LiteLLM model wrappers."""
        kwargs: dict[str, object] = {
            "api_base": self.base_url,
            "api_key": self.api_key,
        }
        headers = dict(self.default_headers)
        if headers:
            kwargs["extra_headers"] = headers
        return kwargs

    def langchain_openai_kwargs(self) -> dict[str, object]:
        """Return LangChain OpenAI kwargs with request-scoped headers."""
        kwargs = self.openai_kwargs()
        api_key_provider: ModelApiKeyProvider | None = None
        if callable(self.api_key):
            api_key_provider = self.api_key
            # LangChain/OpenAI validates api_key as a concrete string during
            # model construction. Aion resolves JWTs per request, so this
            # placeholder only satisfies construction; the request hook below
            # replaces Authorization with a fresh bearer token before send.
            kwargs["api_key"] = _AION_OPENAI_API_KEY_PLACEHOLDER
        kwargs["http_client"] = _aion_model_http_client(api_key_provider)
        kwargs["http_async_client"] = _aion_model_async_http_client(
            api_key_provider
        )
        return kwargs


def aion_model_base_url() -> str:
    """Return the Aion OpenAI-compatible model API base URL."""
    base_url = api_settings.http_url.rstrip("/")
    return f"{base_url}/v1"


def aion_model_api_key(
        jwt_manager: AionRefreshingJWTManager | None = None,
) -> str:
    """Return a current Aion JWT for OpenAI-compatible model clients."""
    manager = jwt_manager or aion_jwt_manager
    token = manager.get_token_sync()
    if not token:
        raise AionAuthenticationError("Unable to obtain an Aion API token.")
    return token


def aion_model_api_key_provider(
        jwt_manager: AionRefreshingJWTManager | None = None,
) -> ModelApiKeyProvider:
    """Return a per-request API-key provider backed by the JWT manager."""
    return lambda: aion_model_api_key(jwt_manager)


async def aion_jwt_api_key(
        jwt_manager: AionJWTManager | None = None,
) -> str:
    """Return a short-lived JWT for OpenAI-compatible clients."""
    manager = jwt_manager or aion_jwt_manager
    token = await manager.get_token()
    if not token:
        raise AionAuthenticationError("Unable to obtain an Aion API token.")
    return token


def aion_principal_selector() -> str | None:
    """Return the current model-service principal selector, if available.

    Reads the active AionRuntimeContext via AionRuntimeContextRegistry, which is
    populated by aion-server at request entry. Returns None when called outside
    a server context (e.g. during local development or direct script use).

    The returned value is used as the ``Aion-Principal-Selector`` header.
    Expected forms:

    - Agent environment selector: ``aion://agent/environment/<id>``
    - Agent identity selector: ``aion://agent/identity/<id>``
    """
    context = get_aion_runtime_context()
    if context is None:
        return None
    return context.get_principal_selector()


def aion_model_request_headers(
        existing: Mapping[str, str] | None = None,
        *,
        principal_selector_provider: PrincipalSelectorProvider | None = None,
        warn_on_missing: bool = True,
) -> dict[str, str]:
    """Return per-request headers for an Aion model-service call."""
    headers = dict(existing or {})
    if AION_PRINCIPAL_SELECTOR_HEADER in headers:
        selector = aion_model_principal_selector_value(
            headers[AION_PRINCIPAL_SELECTOR_HEADER]
        )
        if selector:
            headers[AION_PRINCIPAL_SELECTOR_HEADER] = selector
        else:
            headers.pop(AION_PRINCIPAL_SELECTOR_HEADER, None)
        return headers

    provider = principal_selector_provider or aion_principal_selector
    selector = provider()
    if selector:
        model_selector = aion_model_principal_selector_value(selector)
        if model_selector:
            headers[AION_PRINCIPAL_SELECTOR_HEADER] = model_selector
    elif warn_on_missing:
        logger.warning(
            "Aion model service was called without principal attribution; "
            "control plane policies for a specific agent principal may not "
            "be enforced for this request."
        )
    return headers


def aion_model_principal_selector_value(selector: str) -> str | None:
    """Return a model-service-safe principal selector value.

    Model invocation may run as the authenticated user or as an agent identity.
    The Python SDK intentionally does not send agent-environment selectors to
    the model service. When a runtime context falls back to an environment
    selector, the request proceeds without the selector so the JWT can still be
    evaluated by the server.

    Args:
        selector: Candidate Aion principal selector URI.

    Returns:
        A canonical selector URI when it is valid for model-service requests,
        otherwise ``None``.
    """
    try:
        principal = PrincipalSelector.from_header_value(selector)
    except ValueError as exc:
        logger.error(
            "Aion model service principal selector %r is invalid and will not "
            "be sent: %s",
            selector,
            exc,
        )
        return None

    if principal.kind == PrincipalSelectorKind.AGENT_ENVIRONMENT:
        logger.error(
            "Aion model service resolved agent environment principal selector "
            "%r. Model-service requests require user credentials or an agent "
            "identity selector; the environment selector will not be sent.",
            selector,
        )
        return None

    return principal.to_header_value()


def aion_model_request_hook(request: httpx.Request) -> None:
    """Inject the current principal selector into an outgoing model request."""
    if AION_PRINCIPAL_SELECTOR_HEADER in request.headers:
        selector = aion_model_principal_selector_value(
            request.headers[AION_PRINCIPAL_SELECTOR_HEADER]
        )
        if selector:
            request.headers[AION_PRINCIPAL_SELECTOR_HEADER] = selector
        else:
            del request.headers[AION_PRINCIPAL_SELECTOR_HEADER]
        return
    request.headers.update(aion_model_request_headers(request.headers))


def aion_openai_config() -> AionModelClientConfig:
    """Build config for OpenAI-compatible model clients."""
    return AionModelClientConfig(
        base_url=aion_model_base_url(),
        api_key=aion_model_api_key_provider(),
    )


def _aion_model_http_client(
        api_key_provider: ModelApiKeyProvider | None,
) -> httpx.Client:
    """Create an HTTPX client with runtime model headers."""
    return httpx.Client(
        event_hooks={"request": [_model_request_hook(api_key_provider)]}
    )


def _aion_model_async_http_client(
        api_key_provider: ModelApiKeyProvider | None,
) -> httpx.AsyncClient:
    """Create an async HTTPX client with runtime model headers."""
    return httpx.AsyncClient(
        event_hooks={"request": [_async_model_request_hook(api_key_provider)]}
    )


def _model_request_hook(
        api_key_provider: ModelApiKeyProvider | None,
) -> Callable[[httpx.Request], None]:
    """Return a sync request hook for dynamic auth and principal headers."""
    def hook(request: httpx.Request) -> None:
        if api_key_provider is not None:
            request.headers["Authorization"] = f"Bearer {api_key_provider()}"
        aion_model_request_hook(request)

    return hook


def _async_model_request_hook(
        api_key_provider: ModelApiKeyProvider | None,
) -> Callable[[httpx.Request], Any]:
    """Return an async request hook for dynamic auth and principal headers."""
    async def hook(request: httpx.Request) -> None:
        if api_key_provider is not None:
            request.headers["Authorization"] = f"Bearer {api_key_provider()}"
        aion_model_request_hook(request)

    return hook


__all__ = [
    "AION_PRINCIPAL_SELECTOR_HEADER",
    "AionModelClientConfig",
    "ModelApiKeyProvider",
    "PrincipalSelectorProvider",
    "aion_jwt_api_key",
    "aion_model_api_key",
    "aion_model_api_key_provider",
    "aion_model_principal_selector_value",
    "aion_model_request_hook",
    "aion_model_request_headers",
    "aion_model_base_url",
    "aion_openai_config",
    "aion_principal_selector",
]
