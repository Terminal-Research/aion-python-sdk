"""Sending the files of a conversation to a model by their content.

A file the server stored reaches an agent as ``file_data`` with the file's
address. The address is a download link of the deployment's storage with a
limited lifetime, and a model provider cannot be relied on to fetch it.
``inline_files`` replaces every such part of a model request with
``inline_data`` carrying the file's content. The session keeps the address:
the content is put into the request only.

A tool's media - an image or a document ADK puts into the tool's function
response (``FunctionResponse.parts``) - is kept by its address too, as
``file_data`` of the function response, and ``inline_files`` gives it back its
content as ``inline_data``: that is the one form in which ADK's ``LiteLlm``
sends a tool's media to a model.

The server names the stored file behind each address in the session, in the
``custom_metadata`` of the event that holds it (``STORED_FILES_KEY``), and a
file is read by that name, through a new link once the address's own has
expired. An address the session names no file for is read through the
conversation's tasks, which hold the files the agent was sent and sent.

The model ``aion_lite_llm`` builds does this on every call. An agent with a
model of its own uses the callback:

    from aion.adk.authoring import inline_files_before_model

    agent = LlmAgent(..., before_model_callback=inline_files_before_model)

Every file of the request is sent, on every call: a model keeps nothing
between requests. A file that cannot be read, or is larger than
``AION_MODEL_FILE_MAX_BYTES``, fails the task with a reason its caller reads
(``aion.core.agent.model_files``).

How the content then travels is the model class's own. ADK's ``LiteLlm``
puts an image, audio, video or text file into the request itself, and for an
OpenAI model uploads any other document through the provider's Files API
first, on every request. ``aion_lite_llm`` does not let it: a document is
handed to ``LiteLlm`` as a text mark, and the client that sends the finished
request puts the document into the request's body in the mark's place
(``aion.core.agent.model_files.HeldDocuments``). Aion's model service is not
a provider's Files API, and a document sent with the request leaves no copy
behind.
"""

from __future__ import annotations

from typing import Any, Optional

from aion.core.a2a.files import StoredFile
from aion.core.agent.model_files import HeldDocuments, read_file_for_model
from google.genai import types

__all__ = ["STORED_FILES_KEY", "inline_files", "inline_files_before_model"]

STORED_FILES_KEY = "aion:files"
"""The key of an event's ``custom_metadata`` under which the server names, by
address, the stored files the event's parts link to."""

FALLBACK_MIME_TYPE = "application/octet-stream"

IN_REQUEST_MEDIA = ("image/", "audio/", "video/", "text/")
"""The media types a model class puts into the request itself; any other file is a document."""


def _address(part: Any) -> Optional[str]:
    """The address of a part - or a function response's media - that carries a file by ``http(s)`` address, or None."""
    file_data = part.file_data
    if file_data is None or not file_data.file_uri:
        return None
    return file_data.file_uri if file_data.file_uri.startswith(("http://", "https://")) else None


def _stored_files() -> dict[str, StoredFile]:
    """The stored files the invocation's session names, by address; empty outside an invocation."""
    from aion.adk.authoring.invocation import get_adk_ctx

    ctx = get_adk_ctx()
    session = getattr(ctx, "session", None)
    stored: dict[str, StoredFile] = {}
    for event in getattr(session, "events", None) or []:
        named = (event.custom_metadata or {}).get(STORED_FILES_KEY)
        for url, payload in (named.items() if isinstance(named, dict) else ()):
            reference = StoredFile.from_metadata(payload)
            if reference is not None:
                stored[url] = reference
    return stored


def _is_document(mime_type: Optional[str]) -> bool:
    return not (mime_type or "").lower().startswith(IN_REQUEST_MEDIA)


async def inline_files(llm_request: Any, *, documents: Optional[HeldDocuments] = None) -> None:
    """Replace every file a model request carries by its address with the file's content.

    The request gets new ``Content`` objects for the messages that change;
    the ones it was built from, and the session's events, are left as they
    are.

    Args:
        llm_request: The ADK ``LlmRequest`` about to be sent.
        documents: Where to hold the request's documents - the files that
            are not an image, audio, video or text. Each is replaced by a
            text mark, an inline one too, for the client that sends the
            request to put into its body (``HeldDocuments``). Without it a
            document becomes ``inline_data`` like any file, and the model
            class sends it its own way.

    Raises:
        TaskFailedError: A file could not be read, or is larger than a model
            request may carry.
    """
    stored = _stored_files()
    contents = []
    for content in llm_request.contents or []:
        parts = content.parts or []
        inlined = [await _inline(part, documents, stored) for part in parts]
        if all(new is old for new, old in zip(inlined, parts)):
            contents.append(content)
        else:
            contents.append(types.Content(role=content.role, parts=inlined))
    llm_request.contents = contents


async def _inline(
        part: types.Part, documents: Optional[HeldDocuments], stored: dict[str, StoredFile]
) -> types.Part:
    """The part with its file's content, or the part itself when there is nothing to change."""
    if part.function_response is not None:
        return await _with_media_content(part, stored)
    url = _address(part)
    if url is not None:
        name = part.file_data.display_name
        mime_type = part.file_data.mime_type or FALLBACK_MIME_TYPE
        data = await read_file_for_model(url, name=name, stored=stored.get(url))
    elif documents is not None and part.inline_data is not None and part.inline_data.data:
        name = part.inline_data.display_name
        mime_type = part.inline_data.mime_type or FALLBACK_MIME_TYPE
        data = part.inline_data.data
        if not _is_document(mime_type):
            return part
    else:
        return part

    if documents is not None and _is_document(mime_type):
        return types.Part(text=documents.hold(data, mime_type=mime_type, name=name))
    return types.Part(inline_data=types.Blob(data=data, mime_type=mime_type, display_name=name))


async def _with_media_content(part: types.Part, stored: dict[str, StoredFile]) -> types.Part:
    """The function response part with its media given by their content, or the part itself.

    A document stays ``inline_data`` here too: a tool's media reaches a model
    only that way.
    """
    response = part.function_response
    media = list(response.parts or [])
    changed = False
    for index, item in enumerate(media):
        url = _address(item)
        if url is None:
            continue
        name = item.file_data.display_name
        data = await read_file_for_model(url, name=name, stored=stored.get(url))
        media[index] = types.FunctionResponsePart(
            inline_data=types.FunctionResponseBlob(
                data=data, mime_type=item.file_data.mime_type or FALLBACK_MIME_TYPE, display_name=name
            )
        )
        changed = True
    if not changed:
        return part
    return part.model_copy(update={"function_response": response.model_copy(update={"parts": media})})


async def inline_files_before_model(callback_context: Any, llm_request: Any) -> None:
    """``before_model_callback`` that sends the request's files by their content."""
    await inline_files(llm_request)
    return None
