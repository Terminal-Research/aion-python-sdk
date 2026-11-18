"""Files a model request leaves out, and the note that takes their place.

The SDK's model helpers send a model every file of the messages they are
given, on every call (``aion.core.agent.model_files``). A message stays in
the conversation's history, so a file the model cannot take would fail every
later call too, not only the one it came with. Four kinds of file are left
out of a request instead, each with a short text note in its place, so the
model knows a file was there and can say so:

* a file larger than ``AION_MODEL_FILE_MAX_BYTES``: the SDK does not read it
  for a model at all (``too_large_note``).
* a stored file that is not available to the turn's sender: the storage
  service gives access to a file only on behalf of its owner, the usage
  attribution it was stored under, so in a shared conversation the file one
  participant sent is not available to the turns of the others
  (``forbidden_note``).
* a stored file that is no longer stored: past the retention deadline its
  reference records - the SDK stores a conversation's files for 30 days from
  their upload - or removed earlier (``removed_note``).
* a file the model refused. A model's refusal does not say which file it is
  about, so ``RefusedFileSearch`` finds the file by the calls that go through
  without it (``left_out_note``). Only the models the SDK builds do this:
  they make the call, and see how it fails.

The first three hold for every path that sends a conversation's files by their
content, ``inline_files`` included.

What a model's requests showed is kept for the life of the process
(``MEMORY_ENTRIES``, the least recently used dropped first): a refused file is
left out of the next request to the same model right away, without being
read, and a file the model took is not suspected again. Another process, or
this one after a restart, learns it again from its own calls. Which files are
too large is known from the size a stored file's reference records, or else
for the turn (``aion.core.agent.model_files``).

Nothing of this reaches the conversation: the task, the framework's state and
the client see the messages as they were sent, and the note is in the model
request only.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, Union

from aion.core.a2a.files import StoredFile
from aion.core.exceptions import AionError

logger = logging.getLogger(__name__)

__all__ = [
    "CHECK_TEXT",
    "RefusedFileSearch",
    "RequestFile",
    "forbidden_note",
    "forget_verdicts",
    "is_known_refused",
    "is_request_refusal",
    "left_out_note",
    "removed_note",
    "stored_label",
    "too_large_note",
]

CHECK_TEXT = "Reply with OK."
"""The text sent with a file in a request that checks whether the model takes that file alone."""

REFUSAL_STATUS_CODES = frozenset({400, 413, 415, 422})
"""The statuses with which a model service refuses what a request carries.

An HTTP status, or the provider's status a stream error event names
(``is_request_refusal``).
"""

MEMORY_ENTRIES = 10_000
"""How many verdicts on files the process keeps."""


@dataclass(frozen=True)
class RequestFile:
    """A file a model request carries.

    Attributes:
        key: The file's identity: the stored file's version, or a digest of
            what the request carries for it.
        name: The file's name, when it has one.
        mime_type: The file's media type, when it is known.
        stored: The file the server stored, when the request's file is one.
    """

    key: str
    name: Optional[str] = None
    mime_type: Optional[str] = None
    stored: Optional[StoredFile] = None

    @classmethod
    def of(
            cls,
            content: Union[bytes, str],
            *,
            name: Optional[str] = None,
            mime_type: Optional[str] = None,
            stored: Optional[StoredFile] = None,
    ) -> "RequestFile":
        """The file of a request, known by its stored version or else by its media type and ``content``.

        A stored version never changes, its media type included, so it is the
        file whatever link the request reached it by. Any other file is what
        the request carries for it - its bytes, its base64, the address of a
        file the model reads itself - together with its media type: a model
        refuses a file for the type it is sent with, and the same bytes may
        come again with a type the model takes.
        """
        if stored is not None:
            key = f"file:{stored.file_id}:{stored.version_id}"
        else:
            digest = hashlib.sha256(f"{mime_type or ''}\n".encode())
            digest.update(content if isinstance(content, bytes) else content.encode())
            key = f"sha256:{digest.hexdigest()}"
        return cls(key=key, name=name, mime_type=mime_type, stored=stored)


def _described(name: Optional[str], mime_type: Optional[str]) -> str:
    described = f'File "{name}"' if name else "A file"
    return f"{described} ({mime_type})" if mime_type else described


def left_out_note(file: RequestFile) -> str:
    """The text that takes the place of a file the model refused."""
    return f"[{_described(file.name, file.mime_type)} was not accepted by the model and was left out]"


def too_large_note(name: Optional[str], mime_type: Optional[str], limit: int) -> str:
    """The text that takes the place of a file larger than a model request may carry."""
    return f"[{_described(name, mime_type)} is larger than the {limit} bytes a model request may carry and was left out]"


def forbidden_note(name: Optional[str], mime_type: Optional[str]) -> str:
    """The text that takes the place of a stored file that is not available to the turn's sender."""
    return f"[{_described(name, mime_type)} is not available to this sender and was left out]"


def removed_note(name: Optional[str], mime_type: Optional[str]) -> str:
    """The text that takes the place of a stored file that is no longer stored."""
    return f"[{_described(name, mime_type)} is no longer available and was left out]"


def stored_label(stored: Optional[StoredFile]) -> str:
    """How a log line names the stored file behind a file of a request."""
    return f"file {stored.file_id} version {stored.version_id}" if stored is not None else "not stored"


def is_request_refusal(error: BaseException) -> bool:
    """Whether a model call failed on what its request carries, so possibly on a file.

    That is a ``ValueError`` the model class raised while it built the
    request - LangChain's for a content block OpenAI does not take, ADK's
    for a part it cannot convert - or a status of ``REFUSAL_STATUS_CODES``
    from the model service.

    Aion's model service answers every request with HTTP 200 and a stream,
    and reports a failure as the stream's error event, with no HTTP status
    (``_stream_error``). A provider's refusal is an ``api_error`` whose
    message starts with the provider's status
    (``OpenRouter API request failed: 400 Bad Request - ...``,
    ``_provider_status``); that status decides, before any status the client
    library inferred from the message. Any other error event - the
    service's own, such as a model it does not serve - is not a refusal.

    A failure of the SDK itself (any ``AionError``), of access, of a rate
    limit, of the service or of the connection is not a refusal either.
    """
    if isinstance(error, AionError):
        return False
    event = _stream_error(error)
    if event is not None:
        return _provider_status(event) in REFUSAL_STATUS_CODES
    status = _status_code(error)
    if status is not None:
        return status in REFUSAL_STATUS_CODES
    return isinstance(error, ValueError)


_PROVIDER_STATUS = re.compile(r"[^\n:]{1,80} API request failed: (\d{3})\b")
"""The start of the message of a provider's failure that Aion's model service sends, with its status."""


def _stream_error(error: BaseException) -> Optional[Mapping[str, Any]]:
    """The error object of a model service's stream error event, when ``error`` comes from one.

    The OpenAI client raises the event ``{"error": {"message", "type",
    "param", "code"}}`` as an error without an HTTP status whose ``body`` is
    that error object. LiteLLM raises an error of its own while handling it,
    so the event is looked for along the chain of causes as a traceback
    follows it. An error with an HTTP status answered the request itself and
    is not an event.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        body = getattr(current, "body", None)
        has_status = isinstance(getattr(current, "status_code", None), int)
        if not has_status and isinstance(body, Mapping) and isinstance(body.get("type"), str):
            return body
        current = current.__cause__ if current.__cause__ is not None else (
            None if current.__suppress_context__ else current.__context__
        )
    return None


def _provider_status(event: Mapping[str, Any]) -> Optional[int]:
    """The provider's status a stream error event of Aion's model service names, if it names one.

    Only the start of an ``api_error``'s message is read: the provider's own
    answer follows it and may repeat the request, numbers included.
    """
    message = event.get("message")
    if event.get("type") != "api_error" or not isinstance(message, str):
        return None
    match = _PROVIDER_STATUS.match(message)
    return int(match.group(1)) if match else None


def _status_code(error: BaseException) -> Optional[int]:
    """The HTTP status an error of an OpenAI, LiteLLM or HTTPX client carries."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


class _Verdicts:
    """What the process learned about files, by key; the least recently used dropped first.

    Shared between threads: a model called with ``invoke`` runs in a worker
    thread.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._entries: OrderedDict[tuple[str, str], bool] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> Optional[bool]:
        with self._lock:
            verdict = self._entries.get(key)
            if verdict is not None:
                self._entries.move_to_end(key)
            return verdict

    def put(self, key: tuple[str, str], verdict: bool) -> None:
        with self._lock:
            self._entries[key] = verdict
            self._entries.move_to_end(key)
            while len(self._entries) > self._limit:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_verdicts = _Verdicts(MEMORY_ENTRIES)


def is_known_refused(model: str, stored: StoredFile) -> bool:
    """Whether ``model`` refused the stored file before: then it is left out unread."""
    return _verdicts.get((f"model:{model}", RequestFile.of(b"", stored=stored).key)) is False


def forget_verdicts() -> None:
    """Forget every verdict on a file: for a test that starts from a new process."""
    _verdicts.clear()


class RefusedFileSearch:
    """Which files of one model request to leave out, learned from the tries that fail.

    The model class makes each try of the request, leaves out the files
    ``left_out`` names, with a note in place of each, and reports how the try
    ended. A file is a suspect until the model took it: a try that carried it
    went through, now or in an earlier request of this process.

    1. The first try leaves out the files this model refused before.
    2. A try that fails on what the request carries (``is_request_refusal``)
       is made again without one suspect, the newest first. The try that goes
       through shows that the file it left out is the one the model refuses,
       and its answer is the call's answer: nothing else is sent.
    3. When no try without one suspect goes through, more than one file is
       refused. Each suspect is then sent alone, with ``CHECK_TEXT`` and
       nothing else (``failed`` returns them, ``checked`` takes the outcome),
       and the files the model refuses alone are left out of the next try.
       A file the model takes alone goes on to the model.
    4. If the model takes every suspect alone, the request failed on
       something other than a file, and the first failure is raised. So is
       any failure that is not a refusal, at any step, and a failure of the
       try made after the checks.
    """

    def __init__(self, model: str, files: Sequence[RequestFile]) -> None:
        """
        Args:
            model: The model the request goes to: the same file may be
                refused by one model and taken by another.
            files: The files of the request, in the order the request carries
                them; a file carried twice is one file.
        """
        self._model = model
        self._scope = f"model:{model}"
        newest_first: dict[str, RequestFile] = {}
        for file in reversed(files):
            newest_first.setdefault(file.key, file)
        self._files = list(newest_first.values())
        verdicts = {file.key: _verdicts.get((self._scope, file.key)) for file in self._files}
        self._refused = {key for key, verdict in verdicts.items() if verdict is False}
        self._suspects = [file for file in self._files if verdicts[file.key] is None]
        self._tried = 0
        self._without: Optional[RequestFile] = None
        self._checking = False
        self._unchecked: list[RequestFile] = []
        self._first_failure: Optional[BaseException] = None
        self._found: list[tuple[RequestFile, BaseException]] = []

    @property
    def left_out(self) -> frozenset[str]:
        """The keys of the files the next try leaves out."""
        keys = set(self._refused)
        if self._without is not None:
            keys.add(self._without.key)
        return frozenset(keys)

    def failed(self, error: BaseException) -> list[RequestFile]:
        """Take a try that failed with ``error``.

        Returns:
            The files to send alone before the next try, each reported with
            ``checked``; empty when the next try is to be made right away.

        Raises:
            BaseException: ``error`` itself, when it is not a refusal of the
                request, when no file of the request is a suspect, or when
                the files were checked alone already.
        """
        if not is_request_refusal(error) or not self._suspects or self._checking:
            raise error
        if self._first_failure is None:
            self._first_failure = error
        if self._tried < len(self._suspects):
            self._without = self._suspects[self._tried]
            self._tried += 1
            return []
        self._without = None
        self._checking = True
        self._unchecked = list(self._suspects)
        return list(self._suspects)

    def checked(self, file: RequestFile, error: Optional[BaseException]) -> None:
        """Take what sending ``file`` alone showed.

        Args:
            file: One of the files ``failed`` returned.
            error: How the request with the file alone failed; None when it
                went through.

        Raises:
            BaseException: ``error``, when it is not a refusal of the request;
                the first failure, when the last file to check shows that
                the model takes every suspect alone.
        """
        if error is None:
            _verdicts.put((self._scope, file.key), True)
        elif is_request_refusal(error):
            self._refused.add(file.key)
            self._found.append((file, error))
        else:
            raise error
        self._unchecked.remove(file)
        if not self._unchecked and not self._found and self._first_failure is not None:
            raise self._first_failure

    def succeeded(self) -> None:
        """Take the try that went through, and remember what the request showed."""
        if self._without is not None and self._first_failure is not None:
            self._refused.add(self._without.key)
            self._found.append((self._without, self._first_failure))
            self._without = None
        for file in self._files:
            _verdicts.put((self._scope, file.key), file.key not in self._refused)
        for file, error in self._found:
            logger.warning(
                "Model %s did not accept file %r (%s, %s) and refused the request with %s; "
                "a note takes the file's place in its requests from now on.",
                self._model,
                file.name or "file",
                file.mime_type or "unknown type",
                stored_label(file.stored),
                type(error).__name__,
            )
        self._found = []
