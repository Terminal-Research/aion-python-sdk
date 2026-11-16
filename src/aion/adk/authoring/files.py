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
between requests. A file larger than ``AION_MODEL_FILE_MAX_BYTES`` is not
read: a text note takes its part's place - for a tool's media, a text part
right after the function response - and the conversation goes on without
the file (``aion.core.agent.left_out_files``). A file that cannot be read
fails the task with a reason its caller reads (``aion.core.agent.model_files``).

``aion_lite_llm`` also leaves out a file its model refuses, with a note in
its place, from that call on (``prepare_request``, ``leave_out`` and
``check_request`` are what it uses). An agent with a model of its own sees
the refusal as the model's error, and the event with the file stays in the
session: its ``before_model_callback`` leaves the file out of later requests.

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

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Optional, Union

from aion.core.a2a.files import StoredFile
from aion.core.agent.left_out_files import CHECK_TEXT, RequestFile, left_out_note, too_large_note
from aion.core.agent.model_files import HeldDocuments, ModelFileTooLargeError, read_file_for_model
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


@dataclass(frozen=True)
class FilePart:
    """A file a model request carries, and where.

    Attributes:
        content: The index of the request's ``Content`` that holds the file.
        part: The index of the part in that content.
        media: For a tool's media, the index of the file in the function
            response's ``parts``; None for a part of its own.
        name: The file's name.
        mime_type: The file's media type.
        data: The file's content, when the request carries it.
        uri: The address of a file the model class reads itself - a
            provider's own reference, such as a ``gs://`` object.
        mark: The text mark that stands for a document the request holds
            (``HeldDocuments``).
        stored: The stored file the part linked to, when the session named one.
    """

    content: int
    part: int
    media: Optional[int]
    name: Optional[str]
    mime_type: str
    data: Optional[bytes] = None
    uri: Optional[str] = None
    mark: Optional[str] = None
    stored: Optional[StoredFile] = None

    @cached_property
    def file(self) -> RequestFile:
        """The file, as a search for a refused file knows it."""
        identity: Union[bytes, str] = self.data if self.data is not None else (self.uri or "")
        return RequestFile.of(identity, name=self.name, mime_type=self.mime_type, stored=self.stored)


async def inline_files(llm_request: Any, *, documents: Optional[HeldDocuments] = None) -> None:
    """Replace every file a model request carries by its address with the file's content.

    The request gets new ``Content`` objects for the messages that change;
    the ones it was built from, and the session's events, are left as they
    are. A file larger than a model request may carry is replaced by a text
    note that says so.

    Args:
        llm_request: The ADK ``LlmRequest`` about to be sent.
        documents: Where to hold the request's documents - the files that
            are not an image, audio, video or text. Each is replaced by a
            text mark, an inline one too, for the client that sends the
            request to put into its body (``HeldDocuments``). Without it a
            document becomes ``inline_data`` like any file, and the model
            class sends it its own way.

    Raises:
        TaskFailedError: A file could not be read.
    """
    await prepare_request(llm_request, documents=documents)


Unread = Callable[[StoredFile], bool]
"""Says which stored files a request leaves out without reading them."""

_Read = Union[bytes, ModelFileTooLargeError]
"""What reading a file for a model gave: its content, or why it is left out."""


async def prepare_request(
        llm_request: Any, *, documents: Optional[HeldDocuments] = None, unread: Optional[Unread] = None
) -> list[FilePart]:
    """``inline_files``, and the files the request then carries, in their order.

    The files are read at once rather than one after another. A stored file
    ``unread`` names keeps its address: the request leaves it out.
    """
    stored = _stored_files()
    read = await _read_all(_addresses(llm_request.contents or [], stored, unread), stored)
    contents: list[types.Content] = []
    files: list[FilePart] = []
    for position, content in enumerate(llm_request.contents or []):
        parts = content.parts or []
        prepared: list[types.Part] = []
        for part in parts:
            if part.function_response is not None:
                prepared.extend(_with_media_content(part, position, len(prepared), stored, read, files))
            else:
                prepared.append(_inline(part, position, len(prepared), documents, stored, read, files))
        if len(prepared) == len(parts) and all(new is old for new, old in zip(prepared, parts)):
            contents.append(content)
        else:
            contents.append(types.Content(role=content.role, parts=prepared))
    llm_request.contents = contents
    return files


def _addresses(
        contents: Sequence[types.Content], stored: dict[str, StoredFile], unread: Optional[Unread]
) -> dict[str, Optional[str]]:
    """The addresses of the files to read, a function response's media included, each with the file's name."""
    addresses: dict[str, Optional[str]] = {}
    for content in contents:
        for part in content.parts or []:
            items = list(part.function_response.parts or []) if part.function_response is not None else [part]
            for item in items:
                url = _address(item)
                if url is None:
                    continue
                reference = stored.get(url)
                if reference is not None and unread is not None and unread(reference):
                    continue
                addresses.setdefault(url, item.file_data.display_name)
    return addresses


async def _read_all(addresses: dict[str, Optional[str]], stored: dict[str, StoredFile]) -> dict[str, _Read]:
    """The files at ``addresses``, read at once rather than one after another."""
    reads = [asyncio.ensure_future(_read(url, name, stored.get(url))) for url, name in addresses.items()]
    try:
        return dict(zip(addresses, await asyncio.gather(*reads)))
    except BaseException:
        for read in reads:
            read.cancel()
        raise


async def _read(url: str, name: Optional[str], stored: Optional[StoredFile]) -> _Read:
    try:
        return await read_file_for_model(url, name=name, stored=stored)
    except ModelFileTooLargeError as error:
        return error


def _inline(
        part: types.Part,
        content: int,
        index: int,
        documents: Optional[HeldDocuments],
        stored: dict[str, StoredFile],
        read: dict[str, _Read],
        files: list[FilePart],
) -> types.Part:
    """The part with its file's content - or a note in its place - or the part itself when there is nothing to change."""
    url = _address(part)
    if url is not None:
        name = part.file_data.display_name
        mime_type = part.file_data.mime_type or FALLBACK_MIME_TYPE
        reference = stored.get(url)
        if url not in read:
            files.append(FilePart(content, index, None, name, mime_type, uri=url, stored=reference))
            return part
        data = read[url]
        if isinstance(data, ModelFileTooLargeError):
            return types.Part(text=too_large_note(name, mime_type, data.limit))
    elif part.inline_data is not None and part.inline_data.data:
        name = part.inline_data.display_name
        mime_type = part.inline_data.mime_type or FALLBACK_MIME_TYPE
        data = part.inline_data.data
        reference = None
        if documents is None or not _is_document(mime_type):
            files.append(FilePart(content, index, None, name, mime_type, data=data))
            return part
    elif part.file_data is not None and part.file_data.file_uri:
        file_data = part.file_data
        files.append(FilePart(
            content, index, None, file_data.display_name, file_data.mime_type or FALLBACK_MIME_TYPE,
            uri=file_data.file_uri,
        ))
        return part
    else:
        return part

    if documents is not None and _is_document(mime_type):
        mark = documents.hold(data, mime_type=mime_type, name=name)
        files.append(FilePart(content, index, None, name, mime_type, data=data, mark=mark, stored=reference))
        return types.Part(text=mark)
    files.append(FilePart(content, index, None, name, mime_type, data=data, stored=reference))
    return types.Part(inline_data=types.Blob(data=data, mime_type=mime_type, display_name=name))


def _with_media_content(
        part: types.Part,
        content: int,
        index: int,
        stored: dict[str, StoredFile],
        read: dict[str, _Read],
        files: list[FilePart],
) -> list[types.Part]:
    """The function response part with its media given by their content, then a note for each one left out.

    A document stays ``inline_data`` here too: a tool's media reaches a model
    only that way. A note cannot be a function response's media, so it is a
    text part right after the function response.
    """
    response = part.function_response
    media = list(response.parts or [])
    kept: list[types.FunctionResponsePart] = []
    notes: list[types.Part] = []
    changed = False
    for item in media:
        url = _address(item)
        if url is None or url not in read:
            if url is not None:
                files.append(FilePart(
                    content, index, len(kept), item.file_data.display_name,
                    item.file_data.mime_type or FALLBACK_MIME_TYPE, uri=url, stored=stored.get(url),
                ))
            elif item.inline_data is not None and item.inline_data.data:
                blob = item.inline_data
                files.append(FilePart(
                    content, index, len(kept), blob.display_name, blob.mime_type or FALLBACK_MIME_TYPE, data=blob.data,
                ))
            kept.append(item)
            continue
        changed = True
        name = item.file_data.display_name
        mime_type = item.file_data.mime_type or FALLBACK_MIME_TYPE
        data = read[url]
        if isinstance(data, ModelFileTooLargeError):
            notes.append(types.Part(text=too_large_note(name, mime_type, data.limit)))
            continue
        files.append(FilePart(content, index, len(kept), name, mime_type, data=data, stored=stored.get(url)))
        kept.append(types.FunctionResponsePart(
            inline_data=types.FunctionResponseBlob(data=data, mime_type=mime_type, display_name=name)
        ))
    if not changed:
        return [part]
    return [part.model_copy(update={"function_response": response.model_copy(update={"parts": kept})}), *notes]


def leave_out(contents: list[types.Content], files: Sequence[FilePart], keys: Collection[str]) -> list[types.Content]:
    """The prepared contents with a note in place of every file whose key ``keys`` names.

    A tool's media left out leaves its function response, and its note
    follows the function response as a text part.
    """
    chosen = [file for file in files if file.file.key in keys]
    if not chosen:
        return contents
    result = list(contents)
    for position in sorted({file.content for file in chosen}):
        whole = {file.part: file for file in chosen if file.content == position and file.media is None}
        media: dict[int, dict[int, FilePart]] = {}
        for file in chosen:
            if file.content == position and file.media is not None:
                media.setdefault(file.part, {})[file.media] = file
        parts: list[types.Part] = []
        for index, part in enumerate(contents[position].parts or []):
            if index in whole:
                parts.append(types.Part(text=left_out_note(whole[index].file)))
            elif index in media:
                response = part.function_response
                kept = [item for number, item in enumerate(response.parts or []) if number not in media[index]]
                parts.append(part.model_copy(update={"function_response": response.model_copy(update={"parts": kept})}))
                parts.extend(types.Part(text=left_out_note(media[index][number].file)) for number in sorted(media[index]))
            else:
                parts.append(part)
        result[position] = types.Content(role=contents[position].role, parts=parts)
    return result


def marks_of(files: Sequence[FilePart], keys: Collection[str]) -> set[str]:
    """The marks of the held documents among the files ``keys`` names."""
    return {file.mark for file in files if file.mark is not None and file.file.key in keys}


def check_request(model: Optional[str], file: FilePart) -> tuple[Any, HeldDocuments]:
    """A request that checks whether the model takes ``file`` alone, and the documents it holds."""
    from google.adk.models.llm_request import LlmRequest

    held = HeldDocuments()
    if file.data is None:
        part = types.Part(file_data=types.FileData(file_uri=file.uri, mime_type=file.mime_type, display_name=file.name))
    elif _is_document(file.mime_type):
        part = types.Part(text=held.hold(file.data, mime_type=file.mime_type, name=file.name))
    else:
        part = types.Part(inline_data=types.Blob(data=file.data, mime_type=file.mime_type, display_name=file.name))
    request = LlmRequest(model=model, contents=[types.Content(role="user", parts=[part, types.Part(text=CHECK_TEXT)])])
    return request, held


async def inline_files_before_model(callback_context: Any, llm_request: Any) -> None:
    """``before_model_callback`` that sends the request's files by their content."""
    await inline_files(llm_request)
    return None
