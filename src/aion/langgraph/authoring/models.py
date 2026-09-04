"""LangGraph model helpers backed by the Aion model API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aion.api import aion_openai_config

_RESERVED_AION_MODEL_KWARGS = frozenset(
    {
        # Aion resolves the model-service JWT per request so long-lived graph
        # instances do not reuse stale credentials.
        "api_key",
        # Aion chooses the OpenAI-compatible endpoint from the configured
        # control-plane host; overriding it would bypass Aion's model services.
        "base_url",
        # Aion owns static request headers, such as streaming accept headers,
        # and prevents callers from bypassing dynamic principal attribution with
        # caller-supplied static headers.
        "default_headers",
        # Aion installs an async HTTPX client with request hooks that refresh
        # auth and add the runtime principal selector before each request.
        "http_async_client",
        # Aion installs a sync HTTPX client with the same request-scoped auth and
        # principal selector hooks used by the async client.
        "http_client",
    }
)


def aion_chat_model(
    model: str,
    **kwargs: Any,
) -> Any:
    """Create a LangChain chat model configured for Aion.

    Args:
        model: Model ID from the Aion control plane model catalog.
        **kwargs: Additional keyword arguments passed to ``init_chat_model``.
            Use this for model behavior options such as temperature, token
            limits, timeouts, and retries. This helper is for the Aion
            control-plane model-service path; if you need to control connection
            settings directly, call LangChain's framework-specific model proxy
            adapter instead.

            Reserved connection parameters:

            ``api_key``            Aion refreshes the model-service JWT per
                                   request.
            ``base_url``           Aion selects the control-plane model endpoint.
            ``default_headers``    Aion owns static request headers and dynamic
                                   principal attribution.
            ``http_async_client``  Aion installs async HTTPX request hooks for
                                   auth and principal attribution.
            ``http_client``        Aion installs sync HTTPX request hooks for
                                   auth and principal attribution.

    Returns:
        The chat model returned by LangChain's ``init_chat_model``.

    Raises:
        ImportError: If LangChain is not installed.
        ValueError: If Aion-controlled connection parameters are provided.
    """
    _reject_reserved_model_kwargs("aion_chat_model", kwargs)
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise ImportError(
            "aion_chat_model requires LangChain. Install a LangChain package "
            "that provides langchain.chat_models.init_chat_model."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.langchain_openai_kwargs()
    model_kwargs.update(kwargs)
    return init_chat_model(
        model=model,
        model_provider="openai",
        **model_kwargs,
    )


def aion_chat_openai(
    model: str,
    **kwargs: Any,
) -> Any:
    """Create a ``langchain-openai`` ``ChatOpenAI`` configured for Aion.

    Args:
        model: Model ID from the Aion control plane model catalog.
        **kwargs: Additional keyword arguments passed to ``ChatOpenAI``. Use
            this for model behavior options such as temperature, token limits,
            timeouts, and retries. This helper is for the Aion control-plane
            model-service path; if you need to control connection settings
            directly, instantiate ``ChatOpenAI`` or another framework-specific
            model proxy adapter instead.

            Reserved connection parameters:

            ``api_key``            Aion refreshes the model-service JWT per
                                   request.
            ``base_url``           Aion selects the control-plane model endpoint.
            ``default_headers``    Aion owns static request headers and dynamic
                                   principal attribution.
            ``http_async_client``  Aion installs async HTTPX request hooks for
                                   auth and principal attribution.
            ``http_client``        Aion installs sync HTTPX request hooks for
                                   auth and principal attribution.

    Returns:
        A ``ChatOpenAI`` instance backed by Aion's model proxy.

    Raises:
        ImportError: If ``langchain-openai`` is not installed.
        ValueError: If Aion-controlled connection parameters are provided.
    """
    _reject_reserved_model_kwargs("aion_chat_openai", kwargs)
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "aion_chat_openai requires langchain-openai to be installed."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.langchain_openai_kwargs()
    model_kwargs.update(kwargs)
    return ChatOpenAI(model=model, **model_kwargs)


def _reject_reserved_model_kwargs(
    helper_name: str,
    kwargs: Mapping[str, Any],
) -> None:
    """Reject caller overrides for Aion model-service connection settings."""
    reserved = sorted(_RESERVED_AION_MODEL_KWARGS.intersection(kwargs))
    if not reserved:
        return

    names = ", ".join(f"`{name}`" for name in reserved)
    suffix = "s" if len(reserved) > 1 else ""
    raise ValueError(
        f"{helper_name} controls Aion model-service connection settings; "
        f"remove reserved keyword argument{suffix}: {names}."
    )


__all__ = ["aion_chat_model", "aion_chat_openai"]
