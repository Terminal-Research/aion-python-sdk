"""The contents of stored files, kept in memory for a while.

A stored file reaches an agent as an address, and reading it is a download.
The same file is read again and again: a model that takes a file by its
content gets it in every request of the conversation, several times within
one turn. This cache keeps what was read, and what the server itself just
stored, so that a server downloads a file once.

It is one per process. A stored file is immutable, so an entry never goes
stale: it leaves when it has not been read for ``ttl_seconds``, when room is
needed for a newer one, or when the file is withdrawn (``forget``). Another
server of the agent has a cache of its own and downloads the file itself; the
storage service is what they share.

An entry is kept under the file's own identity (``content_key``) - the id of
the stored version, or the address for a file the server did not store - and
found by the address a part holds as well: a model request names a file by
its address alone.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Optional

from a2a.types import Part

from aion.core.a2a.files import StoredFile
from aion.core.settings import file_settings

__all__ = ["FileContents", "content_key", "file_contents"]


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
    """File contents by identity, within a memory budget.

    Args:
        max_bytes: The memory all entries may take together; 0 keeps nothing.
            A file larger than the budget is not kept.
        ttl_seconds: How long an entry stays after it was last read.
        clock: Monotonic seconds.
    """

    def __init__(
            self,
            *,
            max_bytes: int,
            ttl_seconds: float,
            clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_bytes = max_bytes
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # Least recently read first.
        self._entries: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._keys_by_url: dict[str, str] = {}
        self._size = 0
        self._loading: dict[str, asyncio.Future[bytes]] = {}

    @property
    def size(self) -> int:
        """The memory the entries take together, in bytes."""
        return self._size

    def get(self, key: str) -> Optional[bytes]:
        """The content kept under ``key``, or None; a read keeps the entry longer."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        content, read_at = entry
        if self._clock() - read_at > self._ttl_seconds:
            self._drop(key)
            return None
        self._entries[key] = (content, self._clock())
        self._entries.move_to_end(key)
        return content

    def get_by_url(self, url: str) -> Optional[bytes]:
        """The content of the file ``url`` was seen to address, or None."""
        key = self._keys_by_url.get(url)
        return self.get(key) if key is not None else None

    def put(self, key: str, content: bytes, *, url: Optional[str] = None) -> bool:
        """Keep ``content`` under ``key``, findable by ``url`` too; whether it was kept.

        Entries read longest ago make room. A file larger than the whole
        budget is not kept.
        """
        if len(content) > self._max_bytes:
            return False
        self._drop(key)
        while self._entries and self._size + len(content) > self._max_bytes:
            self._drop(next(iter(self._entries)))
        self._entries[key] = (content, self._clock())
        self._size += len(content)
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
            if url is not None:
                self._keys_by_url[url] = key
            return content
        if url is not None:
            content = self.get_by_url(url)
            if content is not None:
                return content
        loading = self._loading.get(key)
        if loading is None:
            loading = asyncio.ensure_future(loader())
            self._loading[key] = loading
            loading.add_done_callback(lambda _: self._loading.pop(key, None))
        content = await asyncio.shield(loading)
        self.put(key, content, url=url)
        return content

    def forget(self, key: str) -> None:
        """Drop the content kept under ``key``, and the addresses it was found by."""
        self._drop(key)

    def clear(self) -> None:
        """Forget every entry."""
        self._entries.clear()
        self._keys_by_url.clear()
        self._size = 0

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._size -= len(entry[0])
        for url in [url for url, kept in self._keys_by_url.items() if kept == key]:
            del self._keys_by_url[url]


_contents: Optional[FileContents] = None


def file_contents() -> FileContents:
    """The process's cache, sized by ``AION_FILE_CACHE_BYTES`` and ``AION_FILE_CACHE_TTL``."""
    global _contents
    if _contents is None:
        _contents = FileContents(
            max_bytes=file_settings.file_cache_bytes,
            ttl_seconds=file_settings.file_cache_ttl,
        )
    return _contents
