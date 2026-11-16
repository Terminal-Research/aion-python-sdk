"""Working download links for the files stored tasks hold.

A stored file part keeps the link it was stored with, and that link stops
working - an hour after the upload, with the Aion backend - long before the
file is removed. The part also names the file (``aion:file``,
``aion.core.a2a.files``), so the link can be issued again. ``StoredFileLinks``
does that in two places:

* what the server hands to a client - a task, a message, a stream event, a
  push notification - leaves with working links: ``refresh`` replaces every
  expired one in a copy, and the stored task stays as it was written;
* an agent reading a file of its conversation gets a link that works:
  ``link_for``.

A link is issued once and reused by everything that asks for the same file
version in this process until shortly before it expires. Issuing is best
effort: a storage backend that issues no links, a refusal, a timeout or a
file past its retention leaves the part with the link it has, exactly what
it carried before the link expired, and the refusal is remembered for a
while so a file that cannot be vouched for is not asked about on every read.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

from a2a.types import Artifact, Part, TaskStatus

from aion.core.a2a.files import StoredFile
from aion.server.files.a2a.part_transformer import part_fields
from aion.server.files.storage import (
    FileUploadManager,
    UploadContext,
    UploadFailure,
    UploadReceipt,
)

logger = logging.getLogger(__name__)

__all__ = ["FRESH_LINK_MARGIN", "StoredFileLinks"]

FRESH_LINK_MARGIN = timedelta(minutes=5)
"""How long a link must still work to be handed out: one closer to its expiry is replaced."""

RENEWAL_TIMEOUT_SECONDS = 5.0
"""How long one answer or one read waits for the links it is missing."""

RETRY_REFUSED_AFTER_SECONDS = 60.0
"""How long a file version whose link was not issued is left alone."""

MAX_REMEMBERED = 2048
"""Links and refusals kept; past it the expired ones go, then the oldest."""

T = TypeVar("T")
_Key = tuple[str, str]


@dataclass(frozen=True)
class _Link:
    """A link issued here and the reference it goes out with."""

    uri: str
    file: StoredFile


class StoredFileLinks:
    """Issues download links to stored files again, and remembers them.

    Args:
        upload_manager: The manager of the storage backend that stored the
            files. Without one nothing is refreshed.
        now: The clock; replaced in tests.
    """

    def __init__(
            self,
            upload_manager: Optional[FileUploadManager],
            *,
            now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._manager = upload_manager
        self._now = now
        self._links: dict[_Key, _Link] = {}
        self._refused: dict[_Key, float] = {}

    @property
    def is_active(self) -> bool:
        """Whether a storage backend is configured to issue links."""
        return self._manager is not None

    async def link_for(self, part: Part, *, context: Optional[UploadContext] = None) -> Optional[str]:
        """A working link to the stored file of ``part``.

        Args:
            part: A file part of a stored task.
            context: Callback scope of the request asking, when it has one.

        Returns:
            The link to use instead of the part's own, or None when the
            part's own is the one to use: it still works, the part names no
            file, or no new link could be issued.
        """
        file = StoredFile.of(part)
        if file is None or not self._needs_link(file):
            return None
        issued = await self._issue({(file.file_id, file.version_id): file}, context)
        link = issued.get((file.file_id, file.version_id))
        return link.uri if link is not None else None

    async def refresh(self, content: T, *, context: Optional[UploadContext] = None) -> T:
        """``content`` with every expired link of a stored file replaced by a working one.

        Args:
            content: A task, a message, an artifact, a task status, a status
                or artifact update; anything else is returned as it is.
            context: Callback scope of the request asking, when it has one.

        Returns:
            ``content`` itself when nothing was replaced, otherwise a copy
            whose parts carry the new links and their expiry. The original
            is never changed.
        """
        return (await self.refresh_many([content], context=context))[0]

    async def refresh_many(self, contents: Sequence[T], *, context: Optional[UploadContext] = None) -> list[T]:
        """:meth:`refresh` for several objects at once, their links issued together."""
        contents = list(contents)
        if self._manager is None:
            return contents
        wanted: dict[_Key, StoredFile] = {}
        for content in contents:
            for part in _parts(content):
                file = StoredFile.of(part)
                if file is not None and self._needs_link(file):
                    wanted.setdefault((file.file_id, file.version_id), file)
        if not wanted:
            return contents
        issued = await self._issue(wanted, context)
        if not issued:
            return contents
        return [self._with_links(content, issued) for content in contents]

    # -- deciding ---------------------------------------------------------------

    def _needs_link(self, file: StoredFile) -> bool:
        """Whether the part's own link is no longer one to hand out, and the file is still there."""
        now = self._now()
        return file.access_expired(now, margin=FRESH_LINK_MARGIN) and not file.retention_expired(now)

    # -- issuing ----------------------------------------------------------------

    async def _issue(self, wanted: dict[_Key, StoredFile], context: Optional[UploadContext]) -> dict[_Key, _Link]:
        """A working link for each wanted file version that has or gets one."""
        found: dict[_Key, _Link] = {}
        missing: dict[_Key, StoredFile] = {}
        now = self._now()
        for key, file in wanted.items():
            link = self._links.get(key)
            if link is not None and not link.file.access_expired(now, margin=FRESH_LINK_MARGIN):
                found[key] = link
            elif not self._recently_refused(key):
                missing[key] = file
        if not missing or self._manager is None:
            return found

        async def renew(key: _Key, file: StoredFile) -> None:
            link = await self._renew(file, context)
            if link is None:
                self._remember_refusal(key)
            else:
                self._remember_link(key, link)
                found[key] = link

        try:
            async with asyncio.timeout(RENEWAL_TIMEOUT_SECONDS):
                await asyncio.gather(*(renew(key, file) for key, file in missing.items()))
        except TimeoutError:
            unanswered = [key for key in missing if key not in found and key not in self._refused]
            for key in unanswered:
                self._remember_refusal(key)
            logger.warning(
                "No new download link for %d stored file(s) within %.0f s; their stored links are kept",
                len(unanswered),
                RENEWAL_TIMEOUT_SECONDS,
            )
        return found

    async def _renew(self, file: StoredFile, context: Optional[UploadContext]) -> Optional[_Link]:
        """Ask the backend for a new link to ``file``; None when it gave none."""
        try:
            outcome = await self._manager.renew_link(file.file_id, file.version_id, context=context)
        except Exception:
            # Identifiers only: a link is a credential and is never logged.
            logger.exception("Could not issue a new download link for stored file %s", file.file_id)
            return None
        if outcome is None:
            return None
        if isinstance(outcome, UploadFailure):
            logger.warning(
                "No new download link for stored file %s: %s%s",
                file.file_id,
                outcome.error_code.value,
                f" - {_brief(outcome.cause)}" if outcome.cause is not None else "",
            )
            return None
        return self._link(file, outcome)

    def _link(self, file: StoredFile, receipt: UploadReceipt) -> Optional[_Link]:
        """The issued link, unless it would already be too old to hand out."""
        renewed = file.with_access(receipt.access_expires_at, receipt.retention_expires_at)
        if not receipt.uri or renewed.access_expired(self._now(), margin=FRESH_LINK_MARGIN):
            logger.warning(
                "The new download link for stored file %s is unusable: it names no address "
                "or expires within %d s",
                file.file_id,
                FRESH_LINK_MARGIN.total_seconds(),
            )
            return None
        return _Link(receipt.uri, renewed)

    # -- remembering ------------------------------------------------------------

    def _recently_refused(self, key: _Key) -> bool:
        refused_at = self._refused.get(key)
        if refused_at is None:
            return False
        if time.monotonic() - refused_at < RETRY_REFUSED_AFTER_SECONDS:
            return True
        del self._refused[key]
        return False

    def _remember_refusal(self, key: _Key) -> None:
        self._refused[key] = time.monotonic()
        if len(self._refused) > MAX_REMEMBERED:
            horizon = time.monotonic() - RETRY_REFUSED_AFTER_SECONDS
            self._refused = {k: at for k, at in self._refused.items() if at >= horizon}

    def _remember_link(self, key: _Key, link: _Link) -> None:
        self._refused.pop(key, None)
        self._links[key] = link
        if len(self._links) > MAX_REMEMBERED:
            now = self._now()
            kept = {k: v for k, v in self._links.items() if not v.file.access_expired(now)}
            while len(kept) > MAX_REMEMBERED:
                del kept[next(iter(kept))]
            self._links = kept

    # -- writing ----------------------------------------------------------------

    @staticmethod
    def _with_links(content: T, issued: dict[_Key, _Link]) -> T:
        """A copy of ``content`` whose stored file parts carry the issued links."""
        if not any(_key(part) in issued for part in _parts(content)):
            return content
        fresh = copy.deepcopy(content)
        for part in _parts(fresh):
            link = issued.get(_key(part))
            if link is None:
                continue
            part.url = link.uri
            link.file.write_to(part)
        return fresh


def _key(part: Part) -> Optional[_Key]:
    file = StoredFile.of(part)
    return (file.file_id, file.version_id) if file is not None else None


def _parts(content: Any) -> Iterable[Part]:
    """Every part of a task, message, artifact, status or update; nothing for anything else."""
    if isinstance(content, Artifact):
        return content.parts
    if isinstance(content, TaskStatus):
        return content.message.parts if content.HasField("message") else []
    return [part for parts, _ in part_fields(content) for part in parts]


def _brief(cause: BaseException) -> str:
    """Why a link was not issued, in a few words."""
    return getattr(cause, "summary", None) or str(cause) or type(cause).__name__
