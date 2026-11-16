"""Storing a file once, however many times one turn asks for it."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from typing import Any

from aion.server.files.storage import (
    FileUpload,
    FileUploadManager,
    UploadContext,
    UploadFailure,
    UploadOutcome,
)

_Key = tuple[str, str, Any]


class TurnUploads:
    """The files one turn has stored, by content, so that each is stored once.

    A file the agent produces can be asked for twice in one turn: the event
    pipeline stores what the agent sends out, and a framework adapter stores
    what its framework is about to keep in its own state
    (``aion.server.files.turn``). Which of the two comes first is the
    framework's order. The second ask for the same content, media type and
    retention gets the outcome of the first - waiting for it while the upload
    is under way - so the file is stored once and both places hold the same
    link.

    The name is not part of a file's identity: the same bytes under two names
    are one file, stored under the name the first ask gave. A file that could
    not be stored is not remembered, so a later ask tries again.
    """

    def __init__(self) -> None:
        self._outcomes: dict[_Key, asyncio.Future[UploadOutcome]] = {}

    async def store_many(
        self,
        manager: FileUploadManager,
        uploads: Sequence[FileUpload],
        *,
        context: UploadContext,
    ) -> list[tuple[UploadOutcome, bool]]:
        """Store the files this turn has not stored yet.

        The upload runs on its own, so a caller that stops waiting does not
        stop it for the others waiting on the same file.

        Args:
            manager: The manager to store through.
            uploads: The files asked for.
            context: Verified projection to store against.

        Returns:
            For each upload, aligned with ``uploads``: its outcome, and
            whether this call stored it - False when an earlier ask did.
        """
        loop = asyncio.get_running_loop()
        waiting: list[asyncio.Future[UploadOutcome]] = []
        stored_here: list[bool] = []
        new: list[FileUpload] = []
        asked: list[tuple[_Key, asyncio.Future[UploadOutcome]]] = []
        for upload in uploads:
            key = _key(upload)
            future = self._outcomes.get(key)
            stored_here.append(future is None)
            if future is None:
                future = loop.create_future()
                self._outcomes[key] = future
                new.append(upload)
                asked.append((key, future))
            waiting.append(future)

        if new:
            storing = asyncio.ensure_future(manager.store_many(new, context=context))
            storing.add_done_callback(lambda done: self._settle(done, asked))
        outcomes = await asyncio.shield(asyncio.gather(*waiting))
        return list(zip(outcomes, stored_here, strict=True))

    def _settle(
        self,
        storing: asyncio.Future[list[UploadOutcome]],
        asked: list[tuple[_Key, asyncio.Future[UploadOutcome]]],
    ) -> None:
        """Hand each waiting ask the outcome of the upload made for it."""
        if storing.cancelled() or storing.exception() is not None:
            for key, future in asked:
                self._forget(key, future)
                if storing.cancelled():
                    future.cancel()
                else:
                    future.set_exception(storing.exception())
            return
        for (key, future), outcome in zip(asked, storing.result(), strict=True):
            if isinstance(outcome, UploadFailure):
                self._forget(key, future)
            future.set_result(outcome)

    def _forget(self, key: _Key, future: asyncio.Future[UploadOutcome]) -> None:
        if self._outcomes.get(key) is future:
            del self._outcomes[key]


def _key(upload: FileUpload) -> _Key:
    """What makes two uploads the same file: content, media type and retention."""
    return (hashlib.sha256(upload.data).hexdigest(), upload.media_type, upload.retention_expires_at)


__all__ = ["TurnUploads"]
