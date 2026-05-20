"""LangGraph model helpers backed by the Aion model API."""

from __future__ import annotations

from typing import Any

from aion.api import aion_openai_config


def aion_chat_model(
    model: str,
    **kwargs: Any,
) -> Any:
    """Create a LangChain chat model configured for Aion.

    Args:
        model: Model ID from the Aion control plane model catalog.
        **kwargs: Additional keyword arguments passed to ``init_chat_model``.

    Returns:
        The chat model returned by LangChain's ``init_chat_model``.

    Raises:
        ImportError: If LangChain is not installed.
    """
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise ImportError(
            "aion_chat_model requires LangChain. Install a LangChain package "
            "that provides langchain.chat_models.init_chat_model."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.openai_kwargs()
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
        **kwargs: Additional keyword arguments passed to ``ChatOpenAI``.

    Returns:
        A ``ChatOpenAI`` instance backed by Aion's model proxy.

    Raises:
        ImportError: If ``langchain-openai`` is not installed.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "aion_chat_openai requires langchain-openai to be installed."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.openai_kwargs()
    model_kwargs.update(kwargs)
    return ChatOpenAI(model=model, **model_kwargs)


__all__ = ["aion_chat_model", "aion_chat_openai"]
