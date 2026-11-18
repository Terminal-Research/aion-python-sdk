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
(``mark_too_large``), so it is not downloaded again only to be left out. It
remembers a stored file that is no longer stored the same way
(``mark_removed``), so the storage service is not asked about it again.

An entry is kept under the file's own identity (``content_key``) - the id of
the stored version, or the address for a file the server did not store - and
found by the address a part holds as well: a model request names a file by
its address alone.

The turn also keeps, for a stored file, where it is stored: its address and
its reference (``StoredFile``). Content that is a stored file's - a graph
that puts a file it read back into its state, by its content - is found by
the bytes alone (``stored_file_with``), so the framework's state links the
stored file again instead of storing a copy.
"""

from __future__ import annotations

import asyncio
import hashlib
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
        self._removed: set[str] = set()
        self._stored: dict[str, tuple[str, StoredFile, Optional[str]]] = {}
        self._keys_by_digest: dict[str, set[str]] = {}
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

    def put(
            self,
            key: str,
            content: bytes,
            *,
            url: Optional[str] = None,
            stored: Optional[StoredFile] = None,
            media_type: Optional[str] = None,
    ) -> bool:
        """Keep ``content`` under ``key``, findable by ``url`` too; whether it was kept.

        A file larger than a model request may carry is not kept.

        Args:
            key: The file's identity (``content_key``).
            content: The file's bytes.
            url: The address a part holds for the file.
            stored: The stored file the part names, kept with ``url`` so the
                content leads back to it (``stored_file_with``).
            media_type: The stored file's media type, kept with ``stored``.
        """
        if not self._keeps or len(content) > file_settings.model_file_max_bytes:
            return False
        with self._lock:
            self._entries[key] = content
            if url is not None:
                self._keys_by_url[url] = key
        self._remember_stored(key, content, url, stored, media_type)
        return True

    def stored_file_with(
            self, content: bytes, *, media_type: Optional[str] = None
    ) -> Optional[tuple[str, StoredFile]]:
        """The address and reference of the one stored file the turn holds with exactly ``content``.

        None when the turn holds no stored file with these bytes, and when it
        holds several: which of them a copy of the bytes came from cannot be
        told, and in a shared conversation they may have different owners.
        Given ``media_type``, also None unless the stored file was kept with
        that media type: a stored version is its bytes and its type, and the
        same bytes with another type are another file.
        """
        if not self._keeps:
            return None
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            keys = self._keys_by_digest.get(digest)
            if keys is None or len(keys) != 1:
                return None
            found = self._stored.get(next(iter(keys)))
        if found is None:
            return None
        url, stored, kept_media_type = found
        if media_type is not None and _bare(kept_media_type) != _bare(media_type):
            return None
        return url, stored

    def stored_file_at(self, url: str) -> Optional[StoredFile]:
        """The stored file the turn holds at ``url``: one it stored, or read through the conversation."""
        if not self._keeps:
            return None
        with self._lock:
            key = self._keys_by_url.get(url)
            found = self._stored.get(key) if key is not None else None
        return found[1] if found is not None else None

    def _remember_stored(
            self,
            key: str,
            content: bytes,
            url: Optional[str],
            stored: Optional[StoredFile],
            media_type: Optional[str] = None,
    ) -> None:
        """Keep where the stored file ``key`` names is stored, findable by its bytes."""
        if not self._keeps or url is None or stored is None:
            return
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            if key in self._stored:
                return
            self._stored[key] = (url, stored, media_type or None)
            self._keys_by_digest.setdefault(digest, set()).add(key)

    async def load(
            self,
            key: str,
            loader: Callable[[], Awaitable[bytes]],
            *,
            url: Optional[str] = None,
            stored: Optional[StoredFile] = None,
            media_type: Optional[str] = None,
    ) -> bytes:
        """The content under ``key``, loading it once when it is not kept.

        Readers that ask for the same file while it loads wait for the one
        download. A failed load is not kept: the next reader tries again.
        Content kept under another key is found by ``url`` as well: a file the
        server stored is kept under its version, and a reader that knows only
        its address asks under the address. ``stored`` and ``media_type`` are
        kept with the content, as by ``put``.
        """
        content = self.get(key)
        if content is not None:
            if url is not None and self._keeps:
                with self._lock:
                    self._keys_by_url[url] = key
            self._remember_stored(key, content, url, stored, media_type)
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
        self.put(key, content, url=url, stored=stored, media_type=media_type)
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

    def is_removed(self, key: str) -> bool:
        """Whether the turn found the file ``key`` names no longer stored."""
        with self._lock:
            return key in self._removed

    def mark_removed(self, key: str) -> bool:
        """Remember that the file ``key`` names is no longer stored; whether it is news to the turn."""
        if not self._keeps:
            return True
        with self._lock:
            if key in self._removed:
                return False
            self._removed.add(key)
            return True


CALL_CONTEXT_KEY = "aion:file-contents"
"""Where a request's call context (``ServerCallContext.state``) holds the file contents of its turn."""

_KEEPS_NOTHING = FileContents(keeps=False)

_current: ContextVar[Optional[FileContents]] = ContextVar("aion_file_contents", default=None)


def _bare(media_type: Optional[str]) -> Optional[str]:
    """A media type without its parameters, in lower case; None for none."""
    if not media_type:
        return None
    return media_type.split(";", 1)[0].strip().lower() or None


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
