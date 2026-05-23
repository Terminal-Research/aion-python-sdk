"""OpenAI-compatible model API configuration helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Mapping

from aion.core.settings import api_settings

from aion.api.exceptions import AionAuthenticationError
from aion.api.http.jwt_manager import (
    AionJWTManager,
    AionRefreshingJWTManager,
    aion_jwt_manager,
)

AION_PRINCIPAL_SELECTOR_HEADER = "Aion-Principal-Selector"
PrincipalSelectorProvider = Callable[[], str | None]
ModelApiKeyProvider = Callable[[], str]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AionModelClientConfig:
    """Configuration for OpenAI-compatible model clients."""

    base_url: str
    api_key: str | ModelApiKeyProvider
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def request_headers(
        self,
        existing: Mapping[str, str] | None = None,
        *,
        warn_on_missing: bool = True,
    ) -> dict[str, str]:
        """Return fresh headers for one model-service request."""
        headers = dict(self.default_headers)
        headers.update(existing or {})
        return aion_model_request_headers(
            headers, warn_on_missing=warn_on_missing
        )

    def openai_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by OpenAI-compatible clients."""
        kwargs: dict[str, object] = {
            "base_url": self.base_url,
            "api_key": self.api_key,
        }
        headers = self.request_headers()
        if headers:
            kwargs["default_headers"] = headers
        return kwargs

    def litellm_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by LiteLLM model wrappers."""
        kwargs: dict[str, object] = {
            "api_base": self.base_url,
            "api_key": self.api_key,
        }
        headers = self.request_headers()
        if headers:
            kwargs["extra_headers"] = headers
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

    This is intentionally a placeholder until runtime-context propagation can
    read the active Aion invocation from a ContextVar.

    The returned value is used as the ``Aion-Principal-Selector`` header.
    Expected forms:

    - Agent environment selector: ``agent-environment:<agent-environment-id>``
    - Agent identity selector: ``agent-identity:<agent-identity-id>``
    """
    # TODO: Resolve from AionRuntimeContext once invocation context propagation
    # is available here. The context exposes distribution/environment/identity
    # records through distributionExtensionPayload helpers.
    return None


def aion_model_request_headers(
    existing: Mapping[str, str] | None = None,
    *,
    principal_selector_provider: PrincipalSelectorProvider | None = None,
    warn_on_missing: bool = True,
) -> dict[str, str]:
    """Return per-request headers for an Aion model-service call."""
    headers = dict(existing or {})
    if AION_PRINCIPAL_SELECTOR_HEADER in headers:
        return headers

    provider = principal_selector_provider or aion_principal_selector
    selector = provider()
    if selector:
        headers[AION_PRINCIPAL_SELECTOR_HEADER] = selector
    elif warn_on_missing:
        logger.warning(
            "Aion model service was called without principal attribution; "
            "control plane policies for a specific agent principal may not "
            "be enforced for this request."
        )
    return headers


def aion_principal_selector_headers() -> dict[str, str]:
    """Return principal selector headers for the current invocation."""
    return aion_model_request_headers()


def aion_openai_config() -> AionModelClientConfig:
    """Build config for OpenAI-compatible model clients."""
    return AionModelClientConfig(
        base_url=aion_model_base_url(),
        api_key=aion_model_api_key_provider(),
    )


__all__ = [
    "AION_PRINCIPAL_SELECTOR_HEADER",
    "AionModelClientConfig",
    "ModelApiKeyProvider",
    "PrincipalSelectorProvider",
    "aion_jwt_api_key",
    "aion_model_api_key",
    "aion_model_api_key_provider",
    "aion_model_request_headers",
    "aion_model_base_url",
    "aion_openai_config",
    "aion_principal_selector",
    "aion_principal_selector_headers",
]
