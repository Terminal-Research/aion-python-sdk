"""Reading a file of the conversation to put it into a model request.

A stored file reaches an agent as an address. A model provider cannot be
relied on to fetch it: the address is that of the deployment's own storage -
one that works only with its authorization, or a download link with a limited
lifetime - and several model APIs take a file only by its content. So the SDK's
model helpers send every file by its content, and read it here: from what the
turn read or stored already (``aion.core.a2a.file_contents``), otherwise from
storage, through the conversation of the turn, which reads a stored file
through a link issued for the turn's sender.

A file that cannot be read fails the turn's task with a reason its caller
can read - a stored file no link could be issued for now
(``ConversationFileUnavailableError``) among them: the model does not
answer without a file the sender sent while it may come back on the next
try. Three kinds of file are left out instead: the SDK's helpers put a
note in the file's place in the request (``aion.core.agent.left_out_files``),
so the conversation goes on without the file.

* A file larger than ``AION_MODEL_FILE_MAX_BYTES`` is not read:
  ``ModelFileTooLargeError`` says so. A stored file's reference records its
  size, so such a file is known without downloading it; any other is known
  once a read reaches the limit, and is not read again in the turn.
* A stored file the storage service does not make available to the turn's
  sender - in a shared conversation, a file another participant sent - is
  not downloaded: ``ModelFileForbiddenError`` says so.
* A stored file that is no longer stored is not downloaded:
  ``ModelFileRemovedError`` says so. The SDK stores a conversation's files
  for 30 days from their upload, and the file's message stays in the
  history; the turn's conversation knows a file is removed by the retention
  deadline its reference records, or by the storage service's answer that
  it no longer exists. The turn remembers such a file, and logs it once.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import logging
import uuid
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from a2a.types import Part

from aion.core.a2a.file_contents import content_key, file_contents
from aion.core.a2a.files import StoredFile
from aion.core.agent.left_out_files import stored_label
from aion.core.exceptions import (
    ConversationFileError,
    ConversationFileForbiddenError,
    ConversationFileRemovedError,
    ConversationFileTooLargeError,
    ConversationUnavailableError,
    TaskFailedError,
)
from aion.core.settings import file_settings

logger = logging.getLogger(__name__)

__all__ = [
    "HeldDocuments",
    "ModelFileForbiddenError",
    "ModelFileRemovedError",
    "ModelFileTooLargeError",
    "holding_documents",
    "on_turn_event_loop",
    "place_held_documents",
    "read_file_for_model",
    "read_file_for_model_sync",
    "stored_file_at",
    "stored_file_holding",
]

SYNC_READ_TIMEOUT_SECONDS = 120.0
"""How long a worker thread waits for the turn's event loop to read a file."""


class ModelFileTooLargeError(TaskFailedError):
    """A file is larger than ``AION_MODEL_FILE_MAX_BYTES``, the most a model request may carry.

    The SDK's model helpers and ``inline_files`` put a note in the file's
    place instead of failing; raised out of them, it fails the task with its
    reason.

    Attributes:
        limit: The limit the file is larger than.
    """

    def __init__(self, label: str, limit: int) -> None:
        super().__init__(f"File {label!r} is larger than the {limit} bytes a model request may carry.")
        self.limit = limit


class ModelFileForbiddenError(TaskFailedError):
    """A stored file is not available to the turn's sender, so a model request cannot carry it.

    The storage service gives access to a stored file only on behalf of its
    owner, the usage attribution it was stored under, and refused the one the
    turn runs under (``ConversationFileForbiddenError``). The SDK's model
    helpers and ``inline_files`` put a note in the file's place instead of
    failing; raised out of them, it fails the task with its reason.
    """

    def __init__(self, label: str) -> None:
        super().__init__(f"File {label!r} is not available to this sender. It could not be sent to the model.")


class ModelFileRemovedError(TaskFailedError):
    """A stored file is no longer stored, so a model request cannot carry it.

    Its retention deadline has passed, or the storage service answered that
    it no longer exists (``ConversationFileRemovedError``). The SDK's model
    helpers and ``inline_files`` put a note in the file's place instead of
    failing; raised out of them, it fails the task with its reason.
    """

    def __init__(self, label: str) -> None:
        super().__init__(f"File {label!r} is no longer available. It could not be sent to the model.")


def _too_large(key: str, label: str, limit: int, stored: Optional[StoredFile]) -> ModelFileTooLargeError:
    """The error for a file too large for a model request; the turn remembers the file, and logs it once."""
    if file_contents().mark_too_large(key):
        logger.warning(
            "File %r (%s) is larger than the %d bytes a model request may carry; it is not sent to the model.",
            label,
            stored_label(stored),
            limit,
        )
    return ModelFileTooLargeError(label, limit)


def _removed(key: str, label: str, stored: Optional[StoredFile]) -> ModelFileRemovedError:
    """The error for a file that is no longer stored; the turn remembers the file, and logs it once."""
    if file_contents().mark_removed(key):
        logger.warning(
            "File %r (%s) is no longer stored; a note takes its place in the model request.",
            label,
            stored_label(stored),
        )
    return ModelFileRemovedError(label)


def _known_too_large(key: str, limit: int, stored: Optional[StoredFile]) -> bool:
    """Whether the file is too large for a model request by what is known without reading it."""
    if stored is not None and stored.byte_size is not None and stored.byte_size > limit:
        return True
    return file_contents().is_too_large(key)


async def read_file_for_model(
        url: str,
        *,
        name: Optional[str] = None,
        stored: Optional[StoredFile] = None,
        media_type: Optional[str] = None,
) -> bytes:
    """The bytes of the file at ``url``, to send to a model in place of the address.

    Args:
        url: The address a file part of the conversation holds.
        name: The file's name, for the reason given when it cannot be sent.
        stored: The stored file the address links to, when the place that
            holds the address keeps it too - a framework's state does
            (``aion.server.files.turn``). The file is then read by it, through
            a link issued for the turn whenever ``url`` is no link that
            works, wherever the address came from; without it the address is
            looked up in the conversation.
        media_type: The file's media type, kept with the content the turn
            reads, so that the same bytes in a later request of the turn lead
            back to the stored file (``stored_file_holding``).

    Raises:
        ModelFileTooLargeError: The file is larger than a model request may
            carry: by the size its reference records, or by a read in this
            turn.
        ModelFileForbiddenError: The storage service does not make the
            stored file available to the turn's sender.
        ModelFileRemovedError: The stored file is no longer stored.
        TaskFailedError: The file could not be read.
    """
    from aion.core.runtime.context.conversation import current_conversation, fetch_file

    limit = file_settings.model_file_max_bytes
    label = name or "file"
    part = _stored_part(url, name, stored, media_type)
    key = content_key(part) if part is not None else f"url:{url}"
    if file_contents().is_removed(key):
        raise _removed(key, label, stored)
    if _known_too_large(key, limit, stored):
        raise _too_large(key, label, limit, stored)
    try:
        try:
            conversation = current_conversation()
        except ConversationUnavailableError:
            # Outside a turn there is no conversation to renew a link through:
            # the address is read as it is.
            async def download() -> bytes:
                return await fetch_file(url, label=label, max_bytes=limit)

            content = await file_contents().load(key, download, url=url)
        else:
            if part is not None:
                content = await conversation.read_file(part, max_bytes=limit)
            else:
                content = await conversation.read_file_at(url, name=name, max_bytes=limit)
    except ConversationFileTooLargeError as error:
        raise _too_large(key, label, limit, stored) from error
    except ConversationFileForbiddenError as error:
        raise ModelFileForbiddenError(label) from error
    except ConversationFileRemovedError as error:
        raise _removed(key, label, stored) from error
    except ConversationFileError as error:
        raise TaskFailedError(f"{error} It could not be sent to the model.") from error
    if len(content) > limit:
        raise _too_large(key, label, limit, stored)
    return content


def _stored_part(
        url: str, name: Optional[str], stored: Optional[StoredFile], media_type: Optional[str] = None
) -> Optional[Part]:
    """The part a stored file is read by: its address, its name, its media type and the reference to it."""
    if stored is None:
        return None
    part = Part(url=url, filename=name or "", media_type=media_type or "")
    stored.write_to(part)
    return part


def read_file_for_model_sync(
        url: str,
        *,
        name: Optional[str] = None,
        stored: Optional[StoredFile] = None,
        media_type: Optional[str] = None,
) -> bytes:
    """``read_file_for_model`` for code that cannot ``await``: a model called with ``invoke``.

    A framework runs such code in a worker thread while the turn's event loop
    goes on. The read is handed to that loop and waited for here, so it is the
    same read ``read_file_for_model`` makes: from memory, or through the
    turn's conversation, whatever became of the link. Code on another event
    loop hands the read to the turn's loop the same way.

    Called on the turn's event loop itself - ``invoke`` inside a coroutine -
    the read cannot be handed anywhere: the loop would wait for itself. A
    stored file's address works only with authorization, and a download on
    the loop would hold every turn of the process, so nothing is read, not
    even from memory: the task fails at once with a reason that says to
    ``await`` the model instead. The same call fails on the first turn with a
    file, not only once a file of an earlier turn is no longer in memory.
    Outside a turn the file comes from memory or is fetched from its address
    as it is.

    Raises:
        ModelFileTooLargeError: As ``read_file_for_model``.
        ModelFileForbiddenError: As ``read_file_for_model``, when the file is
            read through the turn's conversation.
        ModelFileRemovedError: As ``read_file_for_model``.
        TaskFailedError: As ``read_file_for_model``, and on the turn's event
            loop.
    """
    from aion.core.runtime.context.conversation import current_conversation

    if on_turn_event_loop():
        raise TaskFailedError(
            f"File {name or 'file'!r} could not be read: a model called without await, with invoke() or stream(), "
            "runs on the turn's event loop and cannot read it there. Call ainvoke() or astream() in an async "
            "node, or call the model from a synchronous node. It could not be sent to the model."
        )
    try:
        loop = current_conversation().loop
    except ConversationUnavailableError:
        loop = None
    if loop is not None and loop.is_running():
        reading = asyncio.run_coroutine_threadsafe(
            read_file_for_model(url, name=name, stored=stored, media_type=media_type), loop
        )
        try:
            return reading.result(timeout=SYNC_READ_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError as error:
            reading.cancel()
            raise TaskFailedError(
                f"File {name or 'file'!r} could not be read in time. It could not be sent to the model."
            ) from error
    return _read_from_address(url, name=name, stored=stored, media_type=media_type)


def on_turn_event_loop() -> bool:
    """Whether the code runs on the event loop of the turn in progress.

    Code there cannot wait for anything the turn's loop would do, the read of
    a stored file included, and what it does without ``await`` holds every
    turn the loop serves. False outside a turn, in a worker thread, and on
    another event loop.
    """
    from aion.core.runtime.context.conversation import current_conversation

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return False
    try:
        return current_conversation().loop is running
    except ConversationUnavailableError:
        return False


def stored_file_at(url: str) -> Optional[StoredFile]:
    """The stored file at an address a model request names, when the turn stored or read it there.

    ADK's run request names the files of the message by their addresses, and
    the session names the stored file behind an address only for a file it
    stored itself; the turn knows the stored file of every address it stored
    or read (``aion.core.a2a.file_contents``). Known by its stored file, the
    file is the same in this turn and in the next - what a model's requests
    showed about it holds in both (``aion.core.agent.left_out_files``). None
    outside a turn and for an address the turn holds no stored file at.
    """
    return file_contents().stored_file_at(url)


def stored_file_holding(content: bytes, media_type: Optional[str]) -> Optional[StoredFile]:
    """The stored file whose bytes a model request carries, when the turn knows exactly one.

    A request may carry a file's bytes rather than its address: a tool
    returns the bytes of a file it read, an ADK event of the invocation keeps
    its content while the session links it, a graph sends a model again the
    blocks ``inline_files`` gave it. The turn keeps the bytes of every stored file it stored or read,
    with its media type (``aion.core.a2a.file_contents``); bytes that lead
    back to one of them, with the same media type, are that stored file. A
    later turn has the file by its address, and knows it by the same stored
    file - what a model's requests showed about it holds there too
    (``aion.core.agent.left_out_files``). None outside a turn, and when no
    stored file of the turn, or more than one, has these bytes.
    """
    found = file_contents().stored_file_with(content, media_type=media_type or "")
    return found[1] if found is not None else None


def _read_from_address(
        url: str, *, name: Optional[str], stored: Optional[StoredFile] = None, media_type: Optional[str] = None
) -> bytes:
    """The file from memory, or fetched from its address as it is, without ``await``.

    Only public addresses are fetched, as by ``fetch_file``.
    """
    import httpx

    from aion.core.http import file_transport
    from aion.core.runtime.context.conversation import FILE_READ_TIMEOUT_SECONDS, MAX_FILE_REDIRECTS

    limit = file_settings.model_file_max_bytes
    label = name or "file"
    contents = file_contents()
    part = _stored_part(url, name, stored, media_type)
    key = content_key(part) if part is not None else f"url:{url}"
    if contents.is_removed(key):
        raise _removed(key, label, stored)
    if _known_too_large(key, limit, stored):
        raise _too_large(key, label, limit, stored)
    content = contents.get_by_url(url)
    if content is None and part is not None:
        content = contents.get(key)
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
            logger.debug("Downloaded file %r (%d bytes) from %s", label, size, urlsplit(url).hostname)
            contents.put(key, content, url=url, stored=stored, media_type=media_type)
    if content is None or len(content) > limit:
        raise _too_large(key, label, limit, stored)
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

    def without(self, marks: Collection[str]) -> "HeldDocuments":
        """These documents but the ones ``marks`` stand for: those a request leaves out."""
        return HeldDocuments({mark: held for mark, held in self._documents.items() if mark not in marks})

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
def holding_documents(held: Optional[HeldDocuments] = None) -> Iterator[HeldDocuments]:
    """Hold the documents of the model request made inside the block: ``held``, or none yet."""
    held = held if held is not None else HeldDocuments()
    token = _held_documents.set(held)
    try:
        yield held
    finally:
        _held_documents.reset(token)


def place_held_documents(messages: list[Any]) -> list[Any]:
    """Put the documents held for the request being sent into its messages; see ``HeldDocuments``."""
    held = _held_documents.get()
    return held.place(messages) if held is not None else messages
