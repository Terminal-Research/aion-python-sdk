"""The contents of the files one turn reads, kept for the turn.

A stored file reaches an agent as an address, and reading it is a download.
Within one turn the same file is read again and again: a model that takes a
file by its content gets it in every request of the turn, and a turn that
calls tools makes several. ``FileContents`` keeps what the turn read, and what
the server stored for it - the files of the request that started the turn and
the files the agent sent - so that a turn downloads a file at most once. It
ends with the turn: the next turn reads a file again, from its storage, on
whichever server runs it, and nothing waits in memory between turns.

The request opens it. The inbound preprocessor stores the request's files
while it is current and leaves it on the request's call context
(``CALL_CONTEXT_KEY``); the request executor runs the turn with it
(``keeping_file_contents``), so the files just stored are not downloaded
back. A request carried to another server reaches it without them, and that
server reads the files from storage. Outside a turn ``file_contents`` gives
one that keeps nothing, and every read downloads.

A file larger than ``AION_MODEL_FILE_MAX_BYTES`` is not kept: no model
request carries it. The turn remembers such a file instead
(``mark_too_large``), so it is not downloaded again only to be left out.

An entry is kept under the file's own identity (``content_key``) - the id of
the stored version, or the address for a file the server did not store - and
found by the address a part holds as well: a model request names a file by
its address alone.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from a2a.types import Part

from aion.core.a2a.files import StoredFile
from aion.core.settings import file_settings

__all__ = ["CALL_CONTEXT_KEY", "FileContents", "content_key", "file_contents", "keeping_file_contents"]


def content_key(part: Part) -> Optional[str]:
    """The identity of the file a part addresses; None for a part without an address.

    A file the server stored is the version it stored, whatever link the part
    holds to it. Any other address is its own identity.
    """
    if part.WhichOneof("content") != "url":
        return None
    stored = StoredFile.of(part)
    if stored is not None:
        return f"file:{stored.file_id}:{stored.version_id}"
    return f"url:{part.url}"


class FileContents:
    """The file contents of one turn, by identity; shared with the threads the turn runs code in."""

    def __init__(self, *, keeps: bool = True) -> None:
        """
        Args:
            keeps: False for the one outside a turn, which keeps nothing.
        """
        self._keeps = keeps
        self._entries: dict[str, bytes] = {}
        self._keys_by_url: dict[str, str] = {}
        self._too_large: set[str] = set()
        self._loading: dict[str, asyncio.Future[bytes]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[bytes]:
        """The content kept under ``key``, or None."""
        with self._lock:
            return self._entries.get(key)

    def get_by_url(self, url: str) -> Optional[bytes]:
        """The content of the file ``url`` was seen to address, or None."""
        with self._lock:
            key = self._keys_by_url.get(url)
            return self._entries.get(key) if key is not None else None

    def put(self, key: str, content: bytes, *, url: Optional[str] = None) -> bool:
        """Keep ``content`` under ``key``, findable by ``url`` too; whether it was kept.

        A file larger than a model request may carry is not kept.
        """
        if not self._keeps or len(content) > file_settings.model_file_max_bytes:
            return False
        with self._lock:
            self._entries[key] = content
            if url is not None:
                self._keys_by_url[url] = key
        return True

    async def load(
            self,
            key: str,
            loader: Callable[[], Awaitable[bytes]],
            *,
            url: Optional[str] = None,
    ) -> bytes:
        """The content under ``key``, loading it once when it is not kept.

        Readers that ask for the same file while it loads wait for the one
        download. A failed load is not kept: the next reader tries again.
        Content kept under another key is found by ``url`` as well: a file the
        server stored is kept under its version, and a reader that knows only
        its address asks under the address.
        """
        content = self.get(key)
        if content is not None:
            if url is not None and self._keeps:
                with self._lock:
                    self._keys_by_url[url] = key
            return content
        if url is not None:
            content = self.get_by_url(url)
            if content is not None:
                return content
        if not self._keeps:
            return await loader()
        loading = self._loading.get(key)
        if loading is None:
            loading = asyncio.ensure_future(loader())
            self._loading[key] = loading
            loading.add_done_callback(lambda _: self._loading.pop(key, None))
        content = await asyncio.shield(loading)
        self.put(key, content, url=url)
        return content

    def is_too_large(self, key: str) -> bool:
        """Whether the turn found the file ``key`` names larger than a model request may carry."""
        with self._lock:
            return key in self._too_large

    def mark_too_large(self, key: str) -> bool:
        """Remember that the file ``key`` names is too large for a model request; whether it is news to the turn."""
        if not self._keeps:
            return True
        with self._lock:
            if key in self._too_large:
                return False
            self._too_large.add(key)
            return True


CALL_CONTEXT_KEY = "aion:file-contents"
"""Where a request's call context (``ServerCallContext.state``) holds the file contents of its turn."""

_KEEPS_NOTHING = FileContents(keeps=False)

_current: ContextVar[Optional[FileContents]] = ContextVar("aion_file_contents", default=None)


@contextmanager
def keeping_file_contents(contents: FileContents) -> Iterator[FileContents]:
    """Keep the files read and stored inside the block in ``contents``."""
    token = _current.set(contents)
    try:
        yield contents
    finally:
        _current.reset(token)


def file_contents() -> FileContents:
    """The file contents of the turn running here; outside a turn, one that keeps nothing."""
    return _current.get() or _KEEPS_NOTHING
