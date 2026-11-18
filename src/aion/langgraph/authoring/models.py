"""LangGraph model helpers backed by the Aion model API."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal, Optional, Union, overload

from aion.api import aion_openai_config
from aion.api.callback_errors import reraise_callback_error

if TYPE_CHECKING:
    from langchain.chat_models.base import _ConfigurableModel
    from langchain_openai import ChatOpenAI

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


@overload
def aion_chat_model(
    model: str,
    *,
    configurable_fields: None = None,
    **kwargs: Any,
) -> ChatOpenAI: ...


@overload
def aion_chat_model(
    model: str,
    *,
    configurable_fields: Union[Literal["any"], list[str], tuple[str, ...]],
    **kwargs: Any,
) -> _ConfigurableModel: ...


def aion_chat_model(
    model: str,
    **kwargs: Any,
) -> Union[ChatOpenAI, _ConfigurableModel]:
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
        A ``ChatOpenAI`` backed by Aion's model proxy that sends the files of
        its messages by their content (``aion.langgraph.authoring.files``);
        with ``configurable_fields`` or ``config_prefix``, the configurable
        model LangChain's ``init_chat_model`` returns.

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
    model_kwargs["callbacks"] = _callback_handlers(model_kwargs.get("callbacks"))
    if "configurable_fields" in model_kwargs or "config_prefix" in model_kwargs:
        # A configurable model builds its chat model per call, out of this
        # helper's reach: its graph calls `inline_files` itself.
        return init_chat_model(
            model=model,
            model_provider="openai",
            **model_kwargs,
        )
    return _file_inlining_chat_openai()(model=model, **model_kwargs)


def _file_inlining_chat_openai(chat_openai: Optional[type] = None) -> type:
    """``ChatOpenAI`` - or the ``chat_openai`` class given - that sends the files of its messages by their content.

    Every call replaces the file blocks that carry an address with the file's
    content before the request is built (``aion.langgraph.authoring.files``),
    and leaves out, with a note in its place, a file the model refuses
    (``aion.core.agent.left_out_files``). Built on first use, so that
    importing this module does not need ``langchain-openai``.
    """
    if chat_openai is None:
        try:
            from langchain_openai import ChatOpenAI as chat_openai
        except ImportError as exc:
            raise ImportError(
                "The Aion chat model helpers require langchain-openai to be installed."
            ) from exc

    built = _file_inlining_classes.get(chat_openai)
    if built is not None:
        return built

    from aion.core.agent.left_out_files import RefusedFileSearch, is_known_refused
    from aion.langgraph.authoring.files import check_request, leave_out, prepare_request, prepare_request_sync

    class AionChatOpenAI(chat_openai):
        """``ChatOpenAI`` behind Aion's model service; files go to the model by their content.

        A call whose model refuses a file of its messages is made again
        without that file, a note in its place, and later calls leave the
        file out from the start; the caller sees one call
        (``RefusedFileSearch``). Only the try that goes through reaches the
        call's callbacks: the others fail before their first chunk, and a
        request that checks one file alone is made without callbacks, and
        streamed whatever the call is.
        """

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if _inner_call.get():
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            messages, files = prepare_request_sync(messages, unread=self._refused_before)
            search = RefusedFileSearch(self.model_name, [file.file for file in files])
            while True:
                token = _inner_call.set(True)
                try:
                    return_value = super()._generate(
                        leave_out(messages, files, search.left_out), stop=stop, run_manager=run_manager, **kwargs
                    )
                except Exception as error:
                    failure = error
                else:
                    search.succeeded()
                    return return_value
                finally:
                    _inner_call.reset(token)
                for suspect in search.failed(failure):
                    search.checked(suspect, self._check_alone(_block_of(files, suspect)))

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            if _inner_call.get():
                yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
                return
            messages, files = prepare_request_sync(messages, unread=self._refused_before)
            search = RefusedFileSearch(self.model_name, [file.file for file in files])
            while True:
                chunks = super()._stream(
                    leave_out(messages, files, search.left_out), stop=stop, run_manager=run_manager, **kwargs
                )
                try:
                    first = next(chunks, _NOTHING)
                except Exception as error:
                    failure = error
                else:
                    break
                for suspect in search.failed(failure):
                    search.checked(suspect, self._check_alone(_block_of(files, suspect)))
            search.succeeded()
            if first is not _NOTHING:
                yield first
                yield from chunks

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if _inner_call.get():
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            messages, files = await prepare_request(messages, unread=self._refused_before)
            search = RefusedFileSearch(self.model_name, [file.file for file in files])
            while True:
                token = _inner_call.set(True)
                try:
                    return_value = await super()._agenerate(
                        leave_out(messages, files, search.left_out), stop=stop, run_manager=run_manager, **kwargs
                    )
                except Exception as error:
                    failure = error
                else:
                    search.succeeded()
                    return return_value
                finally:
                    _inner_call.reset(token)
                for suspect in search.failed(failure):
                    search.checked(suspect, await self._acheck_alone(_block_of(files, suspect)))

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            if _inner_call.get():
                async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield chunk
                return
            messages, files = await prepare_request(messages, unread=self._refused_before)
            search = RefusedFileSearch(self.model_name, [file.file for file in files])
            while True:
                chunks = super()._astream(
                    leave_out(messages, files, search.left_out), stop=stop, run_manager=run_manager, **kwargs
                )
                try:
                    first = await anext(chunks, _NOTHING)
                except Exception as error:
                    failure = error
                else:
                    break
                for suspect in search.failed(failure):
                    search.checked(suspect, await self._acheck_alone(_block_of(files, suspect)))
            search.succeeded()
            if first is not _NOTHING:
                yield first
                async for chunk in chunks:
                    yield chunk

        def _refused_before(self, stored):
            """Whether this model refused the stored file before: it is left out without being read."""
            return is_known_refused(self.model_name, stored)

        def _check_alone(self, block):
            """How a request with ``block`` alone failed, or None when the model took it.

            The request is streamed, read to its end: Aion's model service
            answers every request with a stream, which ``ChatOpenAI`` cannot
            read as the answer to a request without one.
            """
            token = _inner_call.set(True)
            try:
                for _ in super()._stream(check_request(block)):
                    pass
            except Exception as error:
                return error
            finally:
                _inner_call.reset(token)
            return None

        async def _acheck_alone(self, block):
            """How a request with ``block`` alone failed, or None when the model took it; streamed, as ``_check_alone``."""
            token = _inner_call.set(True)
            try:
                async for _ in super()._astream(check_request(block)):
                    pass
            except Exception as error:
                return error
            finally:
                _inner_call.reset(token)
            return None

    _file_inlining_classes[chat_openai] = AionChatOpenAI
    return AionChatOpenAI


def _block_of(files, suspect):
    """The file block of ``files`` that carries the file ``suspect`` is."""
    return next(file for file in files if file.file.key == suspect.key)


_inner_call: ContextVar[bool] = ContextVar("aion_chat_openai_inner_call", default=False)
"""Set while ``AionChatOpenAI`` calls the class under it.

A model class may answer one of these methods with another one - LangChain's
``BaseChatModel._agenerate`` runs the model's ``_generate`` in a worker
thread - which reaches the subclass again; that call takes the messages as
they are.
"""

_NOTHING = object()
"""What a stream that ended without a chunk gives as its first one."""


_file_inlining_classes: dict[type, type] = {}
"""The subclass built for each ``ChatOpenAI`` class this module was given."""


def aion_chat_openai(
    model: str,
    **kwargs: Any,
) -> ChatOpenAI:
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
        A ``ChatOpenAI`` instance backed by Aion's model proxy. It sends the
        files of its messages by their content: a file block that carries an
        address is replaced with the file's content in every request
        (``aion.langgraph.authoring.files``).

    Raises:
        ImportError: If ``langchain-openai`` is not installed.
        ValueError: If Aion-controlled connection parameters are provided.
    """
    _reject_reserved_model_kwargs("aion_chat_openai", kwargs)
    chat_openai = _file_inlining_chat_openai()

    config = aion_openai_config()
    model_kwargs = config.langchain_openai_kwargs()
    model_kwargs.update(kwargs)
    model_kwargs["callbacks"] = _callback_handlers(model_kwargs.get("callbacks"))
    return chat_openai(model=model, **model_kwargs)


def _callback_handlers(existing):
    """Preserve typed configuration errors wrapped by the OpenAI transport."""
    from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager

    class CallbackErrors(BaseCallbackHandler):
        raise_error = True
        run_inline = True

        def on_llm_error(self, error: BaseException, **kwargs):
            reraise_callback_error(error)

    if isinstance(existing, BaseCallbackManager):
        manager = existing.copy()
        manager.add_handler(CallbackErrors())
        return manager
    return [*(existing or ()), CallbackErrors()]


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
