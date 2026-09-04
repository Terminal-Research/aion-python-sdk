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
from aion.api.exceptions import AionAuthenticationError, AionModelPrincipalError
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

_IDENTITY_DOCS_URL = "https://docs.aion.to/docs/concepts/identities"

NO_PRINCIPAL_MESSAGE = (
    "This model call carries no Aion principal. Deployment credentials "
    "authenticate the agent version, and the model service does not run work "
    "for a version - it needs the environment's Daemon Identity, which arrives "
    "with an invocation the platform delivers. A direct A2A call to a locally "
    "served agent carries no environment, so a model cannot be reached from "
    f"one. See {_IDENTITY_DOCS_URL}."
)

ENVIRONMENT_PRINCIPAL_MESSAGE = (
    "This invocation's environment has no Daemon Identity, so the only "
    "principal available is the environment itself ({selector}). The model "
    "service accepts a user or an agent identity and never an agent "
    "environment, so the request would be attributed to the agent version and "
    "refused. Assign a Daemon Identity to this environment in the control "
    f"plane. See {_IDENTITY_DOCS_URL}."
)

INVALID_PRINCIPAL_MESSAGE = (
    "Principal selector {selector!r} is not a valid Aion selector, so this "
    "model call cannot be attributed: {reason}"
)


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
    """Return the Aion OpenAI-compatible model API base URL.

    TODO(2026-08-27): this reuses AION_API_HOST as-is - the same host the rest
    of the platform (GraphQL, WS) talks to - with no dedicated endpoint for
    model traffic. No AION_API_HOST has been provisioned/agreed for a real
    deployment yet, so callers relying on this (see aion.server's
    tools_factory.py CODEX_PROVIDER=aion) have nothing to reach until it is.
    Open question: keep sharing AION_API_HOST, or carve out a separate host
    for Codex/model-service traffic.
    """
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
    populated by aion.server at request entry. Returns None when called outside
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
) -> dict[str, str]:
    """Return per-request headers for an Aion model-service call.

    Args:
        existing: Headers to extend. An explicit principal selector here is
            validated the same way as a resolved one.
        principal_selector_provider: Source of the principal selector.
            Defaults to the active Aion runtime context.

    Returns:
        The headers, carrying a principal selector the model service accepts.

    Raises:
        AionModelPrincipalError: When no such principal is available. The
            request is refused server-side in exactly these cases, so it is
            not sent.
    """
    headers = dict(existing or {})
    # Header names are case-insensitive, and httpx hands them over lower-cased,
    # so an explicit selector is found by name rather than by exact key.
    supplied = next(
        (
            key
            for key in headers
            if key.lower() == AION_PRINCIPAL_SELECTOR_HEADER.lower()
        ),
        None,
    )
    if supplied is not None:
        headers[supplied] = aion_model_principal_selector_value(
            headers[supplied], strict=True
        )
        return headers

    provider = principal_selector_provider or aion_principal_selector
    selector = provider()
    if not selector:
        raise AionModelPrincipalError(NO_PRINCIPAL_MESSAGE)

    headers[AION_PRINCIPAL_SELECTOR_HEADER] = aion_model_principal_selector_value(
        selector, strict=True
    )
    return headers


def aion_model_principal_selector_value(
        selector: str,
        *,
        strict: bool = False,
) -> str | None:
    """Return a model-service-safe principal selector value.

    Model invocation may run as the authenticated user or as an agent identity.
    The Python SDK does not send agent-environment selectors to the model
    service, which does not accept them.

    Args:
        selector: Candidate Aion principal selector URI.
        strict: Raise instead of returning ``None`` when the selector cannot be
            sent. Callers building a request that is about to go out use this;
            callers merely normalizing a value do not.

    Returns:
        A canonical selector URI when it is valid for model-service requests,
        otherwise ``None``.

    Raises:
        AionModelPrincipalError: When ``strict`` and the selector is invalid or
            names an agent environment.
    """
    try:
        principal = PrincipalSelector.from_header_value(selector)
    except ValueError as exc:
        if strict:
            raise AionModelPrincipalError(
                INVALID_PRINCIPAL_MESSAGE.format(selector=selector, reason=exc),
                selector=selector,
            ) from exc
        logger.error(
            "Aion model service principal selector %r is invalid and will not "
            "be sent: %s",
            selector,
            exc,
        )
        return None

    if principal.kind == PrincipalSelectorKind.AGENT_ENVIRONMENT:
        if strict:
            raise AionModelPrincipalError(
                ENVIRONMENT_PRINCIPAL_MESSAGE.format(selector=selector),
                selector=selector,
            )
        logger.error(
            "Aion model service resolved agent environment principal selector "
            "%r. Model-service requests require user credentials or an agent "
            "identity selector; the environment selector will not be sent.",
            selector,
        )
        return None

    return principal.to_header_value()


def aion_model_request_hook(request: httpx.Request) -> None:
    """Inject the current principal selector into an outgoing model request.

    Raises:
        AionModelPrincipalError: When the request has no principal the model
            service accepts. Raised from the httpx event hook, so it surfaces
            at the call site that asked for the completion.
    """
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
