"""Google ADK model helpers backed by the Aion model API."""

from __future__ import annotations

from typing import Any

from aion.api import aion_openai_config
from aion.api.model_service_client import (
    aion_model_api_key,
    aion_model_request_headers,
)


def _aion_lite_llm_client(base_client_type: type[Any]) -> Any:
    """Create a LiteLLM client that resolves Aion JWTs per request."""

    class AionLiteLlmClient(base_client_type):
        def completion(
            self,
            *,
            model: str,
            messages: list[Any],
            tools: list[Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            import litellm

            request_kwargs = dict(kwargs)
            # Resolve runtime values at call time: the bearer token may refresh
            # between completions, and principal headers depend on the active
            # Aion runtime context for this request.
            request_kwargs["api_key"] = aion_model_api_key()
            request_kwargs["extra_headers"] = aion_model_request_headers(
                request_kwargs.get("extra_headers")
            )
            return litellm.completion(
                model=model,
                messages=messages,
                tools=tools,
                **request_kwargs,
            )

        async def acompletion(
            self,
            *,
            model: str,
            messages: list[Any],
            tools: list[Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            import litellm

            request_kwargs = dict(kwargs)
            # Resolve runtime values at call time: the bearer token may refresh
            # between completions, and principal headers depend on the active
            # Aion runtime context for this request.
            request_kwargs["api_key"] = aion_model_api_key()
            request_kwargs["extra_headers"] = aion_model_request_headers(
                request_kwargs.get("extra_headers")
            )
            return await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
                **request_kwargs,
            )

    return AionLiteLlmClient()


def aion_lite_llm(
    model: str,
    **kwargs: Any,
) -> Any:
    """Create a Google ADK ``LiteLlm`` instance configured for Aion.

    Args:
        model: Model ID from the Aion control plane model catalog.
        **kwargs: Additional keyword arguments passed to ADK's ``LiteLlm``.

    Returns:
        A Google ADK ``LiteLlm`` instance backed by Aion's model proxy.

    Raises:
        ImportError: If ``google-adk`` is not installed.
    """
    try:
        from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
    except ImportError as exc:
        raise ImportError(
            "aion_lite_llm requires google-adk with LiteLlm support."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.litellm_kwargs()
    model_kwargs.pop("api_key", None)
    model_kwargs["llm_client"] = _aion_lite_llm_client(LiteLLMClient)
    model_kwargs.update(kwargs)
    return LiteLlm(model=model, **model_kwargs)


__all__ = ["aion_lite_llm"]
