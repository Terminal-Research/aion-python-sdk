"""Reading a file of the conversation to put it into a model request.

A stored file reaches an agent as an address. A model provider cannot be
relied on to fetch it: the address is a download link of the deployment's own
storage, with a limited lifetime, and several model APIs take a file only by
its content. So the SDK's model helpers send every file by its content, and
read it here: from memory when this server stored or read it before
(``aion.core.a2a.file_contents``), otherwise from storage, through the
conversation of the turn, which reads a file whatever became of the link the
message holds.

A file that cannot be read, or is larger than ``AION_MODEL_FILE_MAX_BYTES``,
fails the turn's task with a reason its caller can read.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from aion.core.a2a.file_contents import file_contents
from aion.core.exceptions import ConversationFileError, ConversationUnavailableError, TaskFailedError
from aion.core.settings import file_settings

__all__ = [
    "HeldDocuments",
    "holding_documents",
    "place_held_documents",
    "read_file_for_model",
    "read_file_for_model_sync",
]

SYNC_READ_TIMEOUT_SECONDS = 120.0
"""How long a worker thread waits for the turn's event loop to read a file."""


async def read_file_for_model(url: str, *, name: Optional[str] = None) -> bytes:
    """The bytes of the file at ``url``, to send to a model in place of the address.

    Args:
        url: The address a file part of the conversation holds.
        name: The file's name, for the reason given when it cannot be sent.

    Raises:
        TaskFailedError: The file could not be read, or is larger than a
            model request may carry.
    """
    from aion.core.runtime.context.conversation import current_conversation, fetch_file

    limit = file_settings.model_file_max_bytes
    label = name or "file"
    try:
        try:
            conversation = current_conversation()
        except ConversationUnavailableError:
            # Outside a turn there is no conversation to renew a link through:
            # the address is read as it is.
            async def download() -> bytes:
                return await fetch_file(url, label=label, max_bytes=limit)

            content = await file_contents().load(f"url:{url}", download, url=url)
        else:
            content = await conversation.read_file_at(url, name=name, max_bytes=limit)
    except ConversationFileError as error:
        raise TaskFailedError(f"{error} It could not be sent to the model.") from error
    if len(content) > limit:
        raise TaskFailedError(
            f"File {label!r} is larger than the {limit} bytes a model request may carry."
        )
    return content


def read_file_for_model_sync(url: str, *, name: Optional[str] = None) -> bytes:
    """``read_file_for_model`` for code that cannot ``await``: a model called with ``invoke``.

    A framework runs such code in a worker thread while the turn's event loop
    goes on. The read is handed to that loop and waited for here, so it is the
    same read ``read_file_for_model`` makes: from memory, or through the
    turn's conversation, whatever became of the link.

    Called on the event loop itself - ``invoke`` inside a coroutine, which
    blocks the loop it would have to wait for - or outside a turn, the file
    comes from memory or is fetched from its address as it is, and a file
    whose link has expired cannot be read.

    Raises:
        TaskFailedError: As ``read_file_for_model``.
    """
    from aion.core.runtime.context.conversation import current_conversation

    try:
        asyncio.get_running_loop()
        in_worker_thread = False
    except RuntimeError:
        in_worker_thread = True
    try:
        loop = current_conversation().loop
    except ConversationUnavailableError:
        loop = None

    if in_worker_thread and loop is not None and loop.is_running():
        reading = asyncio.run_coroutine_threadsafe(read_file_for_model(url, name=name), loop)
        try:
            return reading.result(timeout=SYNC_READ_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError as error:
            reading.cancel()
            raise TaskFailedError(
                f"File {name or 'file'!r} could not be read in time. It could not be sent to the model."
            ) from error
    return _read_from_address(url, name=name)


def _read_from_address(url: str, *, name: Optional[str]) -> bytes:
    """The file from memory, or fetched from its address as it is, without ``await``.

    Only public addresses are fetched, as by ``fetch_file``.
    """
    import httpx

    from aion.core.http import file_transport
    from aion.core.runtime.context.conversation import FILE_READ_TIMEOUT_SECONDS, MAX_FILE_REDIRECTS

    limit = file_settings.model_file_max_bytes
    label = name or "file"
    contents = file_contents()
    content = contents.get_by_url(url)
    if content is None:
        chunks: list[bytes] = []
        size = 0
        try:
            with httpx.Client(
                    transport=file_transport.file_transport(),
                    trust_env=False,
                    timeout=FILE_READ_TIMEOUT_SECONDS,
                    follow_redirects=True,
                    max_redirects=MAX_FILE_REDIRECTS,
            ) as client:
                with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise TaskFailedError(
                            f"File {label!r} could not be read: its address answered HTTP "
                            f"{response.status_code}. It could not be sent to the model."
                        )
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            break
                        chunks.append(chunk)
        except file_transport.AddressNotPublicError as error:
            raise TaskFailedError(
                f"File {label!r} could not be read: its address is not a public one, and files "
                "are downloaded from public addresses only. It could not be sent to the model."
            ) from error
        except httpx.HTTPError as error:
            raise TaskFailedError(
                f"File {label!r} could not be read: {type(error).__name__}. It could not be sent to the model."
            ) from error
        if size <= limit:
            content = b"".join(chunks)
            contents.put(f"url:{url}", content, url=url)
    if content is None or len(content) > limit:
        raise TaskFailedError(
            f"File {label!r} is larger than the {limit} bytes a model request may carry."
        )
    return content


# -- a document in an OpenAI-format request ---------------------------------

@dataclass
class HeldDocuments:
    """The documents of one model request, held until the request is built.

    A model class that would send a document its own way - ADK's ``LiteLlm``
    uploads one to the provider's Files API when the model is OpenAI's - is
    given a short text mark in its place (``hold``). The client that sends
    the finished request swaps each mark for the document, in the request's
    body (``place``), so the document travels the way the SDK decides: once,
    with the request, and nothing is left at the provider.
    """

    _documents: dict[str, tuple[bytes, str, Optional[str]]] = field(default_factory=dict)

    def hold(self, content: bytes, *, mime_type: str, name: Optional[str] = None) -> str:
        """Keep a document and return the text mark that stands for it."""
        mark = f"[aion-file:{uuid.uuid4().hex}]"
        self._documents[mark] = (content, mime_type, name)
        return mark

    def place(self, messages: list[Any]) -> list[Any]:
        """The messages of a Chat Completions request with every mark replaced by its document.

        A document becomes a ``file`` content part carrying the file's name
        and its content as a data URL, in the user message its mark is in.

        Raises:
            TaskFailedError: A held document's mark is in no user message, so
                the request would go out without the file.
        """
        if not self._documents:
            return messages
        placed: set[str] = set()
        result = [self._place_in(message, placed) for message in messages]
        missing = [name or "file" for mark, (_, _, name) in self._documents.items() if mark not in placed]
        if missing:
            raise TaskFailedError(
                f"File {missing[0]!r} could not be put into the model request."
            )
        return result

    def _place_in(self, message: Any, placed: set[str]) -> Any:
        if not isinstance(message, dict) or message.get("role") != "user":
            return message
        content = message.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(parts, list) or not any(self._marks_in(part) for part in parts):
            return message
        replaced: list[Any] = []
        for part in parts:
            marks = self._marks_in(part)
            if not marks:
                replaced.append(part)
                continue
            rest = part["text"]
            for mark in marks:
                before, _, rest = rest.partition(mark)
                if before.strip():
                    replaced.append({"type": "text", "text": before})
                replaced.append(self._file_part(mark))
                placed.add(mark)
            if rest.strip():
                replaced.append({"type": "text", "text": rest})
        return {**message, "content": replaced}

    def _marks_in(self, part: Any) -> list[str]:
        """The marks a text part holds, in the order they appear."""
        if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
            return []
        text = part["text"]
        return sorted((mark for mark in self._documents if mark in text), key=text.index)

    def _file_part(self, mark: str) -> dict[str, Any]:
        content, mime_type, name = self._documents[mark]
        data = base64.b64encode(content).decode()
        return {"type": "file", "file": {"filename": name or "file", "file_data": f"data:{mime_type};base64,{data}"}}


_held_documents: ContextVar[Optional[HeldDocuments]] = ContextVar("aion_held_documents", default=None)


@contextmanager
def holding_documents() -> Iterator[HeldDocuments]:
    """Hold the documents of the model request made inside the block."""
    held = HeldDocuments()
    token = _held_documents.set(held)
    try:
        yield held
    finally:
        _held_documents.reset(token)


def place_held_documents(messages: list[Any]) -> list[Any]:
    """Put the documents held for the request being sent into its messages; see ``HeldDocuments``."""
    held = _held_documents.get()
    return held.place(messages) if held is not None else messages
