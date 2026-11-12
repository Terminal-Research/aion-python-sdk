"""Google ADK model helpers backed by the Aion model API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from aion.api import aion_openai_config
from aion.api.callback_errors import reraise_callback_error
from aion.core.agent.model_files import place_held_documents
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
            request_kwargs.setdefault("num_retries", 0)
            messages = place_held_documents(messages)
            try:
                result = litellm.completion(
                    model=_routed(model), messages=messages, tools=tools, **request_kwargs,
                )
                return _callback_stream(result) if request_kwargs.get("stream") else result
            except Exception as error:
                reraise_callback_error(error)
                raise

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
            request_kwargs.setdefault("num_retries", 0)
            messages = place_held_documents(messages)
            try:
                result = await litellm.acompletion(
                    model=_routed(model), messages=messages, tools=tools, **request_kwargs,
                )
                return _async_callback_stream(result) if request_kwargs.get("stream") else result
            except Exception as error:
                reraise_callback_error(error)
                raise

    return AionLiteLlmClient()


def _routed(model: str) -> str:
    """Name ``model`` for LiteLLM so that the model service receives the id as it is.

    LiteLLM reads the first segment of a model name as the provider and
    sends the rest. Aion's model service speaks the OpenAI protocol, and its
    catalog ids have segments of their own (``openai/gpt-...``), so the id is
    given the ``openai/`` provider segment for LiteLLM to take off again.
    """
    return f"openai/{model}"


def _callback_stream(stream: Any) -> Iterator[Any]:
    """Translate iteration-time errors without buffering or retrying output."""
    try:
        yield from stream
    except Exception as error:
        reraise_callback_error(error)
        raise
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


async def _async_callback_stream(stream: Any) -> AsyncIterator[Any]:
    """Keep asynchronous stream errors actionable and release the connection."""
    try:
        async for chunk in stream:
            yield chunk
    except Exception as error:
        reraise_callback_error(error)
        raise
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def aion_lite_llm(
    model: str,
    **kwargs: Any,
) -> Any:
    """Create a Google ADK ``LiteLlm`` instance configured for Aion.

    Args:
        model: Model ID from the Aion control plane model catalog.
        **kwargs: Additional keyword arguments passed to ADK's ``LiteLlm``.

    Returns:
        A Google ADK ``LiteLlm`` instance backed by Aion's model proxy. It
        sends the files of a request by their content: a part that carries a
        file by its address is replaced with the file's content in every
        request (``aion.adk.authoring.files``).

    Raises:
        ImportError: If ``google-adk`` or ``litellm`` is not installed. The
            ADK extras install litellm only on Python < 3.15, the newest it
            supports.
    """
    try:
        from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
    except ImportError as exc:
        raise ImportError(
            "aion_lite_llm requires google-adk and litellm. The ADK extras "
            "install litellm on Python < 3.15 only, the newest it supports."
        ) from exc

    config = aion_openai_config()
    model_kwargs = config.litellm_kwargs()
    model_kwargs.pop("api_key", None)
    model_kwargs["llm_client"] = _aion_lite_llm_client(LiteLLMClient)
    model_kwargs.update(kwargs)
    return _file_inlining_lite_llm(LiteLlm)(model=model, **model_kwargs)


def _file_inlining_lite_llm(lite_llm: type) -> type:
    """``LiteLlm`` that sends the files of a request by their content."""
    built = _file_inlining_classes.get(lite_llm)
    if built is not None:
        return built

    from aion.adk.authoring.files import inline_files
    from aion.core.agent.model_files import holding_documents

    class AionLiteLlm(lite_llm):
        """ADK's ``LiteLlm`` behind Aion's model service; files go to the model by their content."""

        async def generate_content_async(self, llm_request, stream=False):
            # Documents are held here and put into the request's body by the
            # client that sends it (`AionLiteLlmClient`): LiteLlm would upload
            # them to the provider's Files API otherwise.
            with holding_documents() as documents:
                await inline_files(llm_request, documents=documents)
                async for response in super().generate_content_async(llm_request, stream):
                    yield response

    _file_inlining_classes[lite_llm] = AionLiteLlm
    return AionLiteLlm


_file_inlining_classes: dict[type, type] = {}
"""The subclass built for each ``LiteLlm`` class this module was given."""


__all__ = ["aion_lite_llm"]
