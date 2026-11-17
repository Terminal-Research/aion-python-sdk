"""Working download links for the files stored tasks hold, for whom they may be given to.

A stored file part keeps the file's address and names the file
(``aion:file``, ``aion.core.a2a.files``). With the Aion backend the address
is the version's protected content address, which works only with
authorization; a backend whose address is itself a download link records
when it stops working. Either way the part keeps no link that works for
anyone who holds it, and ``StoredFileLinks`` supplies one in two places:

* what the server hands to a client - a task, a message, a stream event, a
  push notification - leaves with working links: ``refresh`` puts one in
  place of every part's address that does not work, in a copy, and the
  stored task stays as it was written;
* an agent reading a file of its conversation gets a link that works:
  ``link_for``.

A link is asked for on behalf of the usage attribution of the request it is
for - the request's scope, which the server keeps on the call context under
``FILE_LINK_SCOPE_KEY`` (``link_scope_of``) - or of the turn an agent reads
in; with neither, on behalf of the deployment's daemon. The Aion Files API
grants a link to the file's owner - the identity of the usage attribution
it was stored under - and refuses the daemon and anyone else. A link is a
credential to whoever holds it, so it is handed out only on behalf of the
attribution it was issued to: the link a file was stored with
(``FileUploadManager.on_stored``) and every link issued later are
remembered for that attribution - by a digest of its carrier, never the
carrier itself - and reused for it until shortly before they expire.
Another attribution asks for its own, and is granted one only if the file
is its owner's.

Issuing is best effort: a storage backend that issues no links, a refusal,
a timeout or a file past its retention leaves the part with the address it
has, and the refusal is remembered for a while for the attribution it was
given to, so a file that cannot be vouched for is not asked about on every
read. What is remembered is the kind of refusal too: the storage service
refusing the grant to a usage attribution (``STORAGE_FORBIDDEN``, its answer
to an attribution that is not the file's owner) means the file is not
available to that sender. ``link_for`` then raises
``ConversationFileForbiddenError``, so a reader does not fetch an address
that would refuse it as well; any other refusal, and any refusal to the
deployment's daemon, leaves the reader with the part's address. What the
server hands out (``refresh``) keeps the part's address on every refusal.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar, Union

from a2a.types import Artifact, Part, TaskStatus

from aion.core.a2a.files import StoredFile
from aion.core.exceptions import AionAuthenticationError, ConversationFileForbiddenError
from aion.core.runtime.context import get_aion_runtime_context
from aion.server.files.a2a.part_transformer import part_fields
from aion.server.files.storage import (
    FileUploadErrorCode,
    FileUploadManager,
    UploadContext,
    UploadFailure,
    UploadReceipt,
)

logger = logging.getLogger(__name__)

__all__ = ["FILE_LINK_SCOPE_KEY", "FRESH_LINK_MARGIN", "StoredFileLinks", "link_scope_of"]

FILE_LINK_SCOPE_KEY = "aion.file_link_scope"
"""Where a request keeps, in ``call_context.state``, the scope the links of its answer are asked for in."""

FRESH_LINK_MARGIN = timedelta(minutes=5)
"""How long a link must still work to be handed out: one closer to its expiry is replaced."""

RENEWAL_TIMEOUT_SECONDS = 5.0
"""How long one answer or one read waits for the links it is missing."""

RETRY_REFUSED_AFTER_SECONDS = 60.0
"""How long a file version whose link was not issued is left alone, for the attribution it was refused to."""

MAX_REMEMBERED = 2048
"""Links and refusals kept; past it the expired ones go, then the oldest."""

T = TypeVar("T")
_Key = tuple[str, str]
_Asker = Optional[str]
"""Who asked for a link: a digest of the usage attribution's carrier, or None for the deployment's daemon."""
_Held = tuple[str, str, _Asker]
"""A file version and the attribution a link to it was issued to."""


def link_scope_of(call_context: Any) -> Optional[UploadContext]:
    """The scope the request of ``call_context`` asks for file links in; None asks as the daemon.

    The request handler keeps it under ``FILE_LINK_SCOPE_KEY`` for every
    request whose answer carries files: the usage attribution the request
    declared, or None when it declared none.
    """
    state = getattr(call_context, "state", None)
    scope = state.get(FILE_LINK_SCOPE_KEY) if isinstance(state, dict) else None
    return scope if isinstance(scope, UploadContext) else None


@dataclass(frozen=True)
class _Link:
    """A link issued here and the reference it goes out with."""

    uri: str
    file: StoredFile


@dataclass(frozen=True)
class _Refusal:
    """A link that was not issued: when, by the monotonic clock, and whether the storage service forbade it.

    ``forbidden`` is the storage service refusing the grant to the
    attribution that asked (``STORAGE_FORBIDDEN``). A timeout, an
    unavailable service, a missing daemon identity, a link too short-lived
    to use and every other failure are not.
    """

    at: float
    forbidden: bool = False


class StoredFileLinks:
    """Issues download links to stored files again, and remembers them.

    Args:
        upload_manager: The manager of the storage backend that stored the
            files. Without one nothing is refreshed. The links it issues
            with each stored file are remembered for the attribution the
            file was stored under.
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
        self._links: dict[_Held, _Link] = {}
        self._refused: dict[_Key, dict[_Asker, _Refusal]] = {}
        if upload_manager is not None:
            upload_manager.on_stored(self._remember_upload)

    @property
    def is_active(self) -> bool:
        """Whether a storage backend is configured to issue links."""
        return self._manager is not None

    async def link_for(self, part: Part, *, context: Optional[UploadContext] = None) -> Optional[str]:
        """A working link to the stored file of ``part``, for the attribution that reads it.

        Args:
            part: A file part of a stored task.
            context: Callback scope of the request asking, when it has one;
                without one, the attribution of the turn the call runs in.

        Returns:
            The link to use instead of the part's address, or None when the
            address is the one to use: it is a link that still works, the
            part names no file, or no link could be issued.

        Raises:
            ConversationFileForbiddenError: The storage service refused the
                link to the usage attribution asking, now or within
                ``RETRY_REFUSED_AFTER_SECONDS``: the file is not available
                to that sender, and its address would refuse it too.
        """
        file = StoredFile.of(part)
        if file is None or not self._needs_link(file):
            return None
        key = (file.file_id, file.version_id)
        asker = _asker(context)
        issued, refused = await self._issue({key: file}, context, asker)
        if key in issued:
            return issued[key].uri
        refusal = refused.get(key)
        if asker is not None and refusal is not None and refusal.forbidden:
            raise ConversationFileForbiddenError(part.filename or "file", part.media_type or None)
        return None

    async def refresh(self, content: T, *, context: Optional[UploadContext] = None) -> T:
        """``content`` with a working link in place of every stored file's address that does not work.

        Args:
            content: A task, a message, an artifact, a task status, a status
                or artifact update; anything else is returned as it is.
            context: Callback scope of the request asking, when it has one.

        Returns:
            ``content`` itself when nothing was replaced, otherwise a copy
            whose parts carry the links and their expiry. The original is
            never changed.
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
        issued, _ = await self._issue(wanted, context, _asker(context))
        if not issued:
            return contents
        return [self._with_links(content, issued) for content in contents]

    # -- deciding ---------------------------------------------------------------

    def _needs_link(self, file: StoredFile) -> bool:
        """Whether the part's address is no link to hand out, and the file is still there.

        An address that works only with credentials records no access
        expiry, and is never one; a link is not, from shortly before it
        expires.
        """
        now = self._now()
        return file.access_expired(now, margin=FRESH_LINK_MARGIN) and not file.retention_expired(now)

    # -- issuing ----------------------------------------------------------------

    async def _issue(
            self, wanted: dict[_Key, StoredFile], context: Optional[UploadContext], asker: _Asker
    ) -> tuple[dict[_Key, _Link], dict[_Key, _Refusal]]:
        """For ``asker``: a working link for each wanted file version that has or gets one, and the refusals.

        A version refused recently is not asked about again: its remembered
        refusal answers. A version the backend gave no link for, or no
        answer in time, is refused too. Without a backend nothing is asked,
        and nothing is refused.
        """
        found: dict[_Key, _Link] = {}
        refused: dict[_Key, _Refusal] = {}
        missing: dict[_Key, StoredFile] = {}
        now = self._now()
        for key, file in wanted.items():
            link = self._links.get((*key, asker))
            if link is not None and not link.file.access_expired(now, margin=FRESH_LINK_MARGIN):
                found[key] = link
                continue
            refusal = self._recent_refusal(key, asker)
            if refusal is None:
                missing[key] = file
            else:
                refused[key] = refusal
        if not missing or self._manager is None:
            return found, refused

        answered: set[_Key] = set()

        async def renew(key: _Key, file: StoredFile) -> None:
            outcome = await self._renew(file, context)
            answered.add(key)
            if isinstance(outcome, _Refusal):
                self._remember_refusal(key, asker, outcome)
                refused[key] = outcome
            else:
                self._remember_link(key, asker, outcome)
                found[key] = outcome

        try:
            async with asyncio.timeout(RENEWAL_TIMEOUT_SECONDS):
                await asyncio.gather(*(renew(key, file) for key, file in missing.items()))
        except TimeoutError:
            unanswered = [key for key in missing if key not in answered]
            for key in unanswered:
                refused[key] = _Refusal(time.monotonic())
                self._remember_refusal(key, asker, refused[key])
            logger.warning(
                "No new download link for %d stored file(s) within %.0f s; their parts keep their addresses",
                len(unanswered),
                RENEWAL_TIMEOUT_SECONDS,
            )
        return found, refused

    async def _renew(self, file: StoredFile, context: Optional[UploadContext]) -> Union[_Link, _Refusal]:
        """Ask the backend for a new link to ``file``: the link, or the refusal when it gave none."""
        try:
            outcome = await self._manager.renew_link(file.file_id, file.version_id, context=context)
        except Exception:
            # Identifiers only: a link is a credential and is never logged.
            logger.exception("Could not issue a new download link for stored file %s", file.file_id)
            return _Refusal(time.monotonic())
        if outcome is None:
            return _Refusal(time.monotonic())
        if isinstance(outcome, UploadFailure):
            logger.warning(
                "No new download link for stored file %s: %s%s",
                file.file_id,
                outcome.error_code.value,
                f" - {_brief(outcome.cause)}" if outcome.cause is not None else "",
            )
            return _Refusal(time.monotonic(), forbidden=outcome.error_code == FileUploadErrorCode.STORAGE_FORBIDDEN)
        return self._link(file, outcome) or _Refusal(time.monotonic())

    def _link(self, file: StoredFile, receipt: UploadReceipt) -> Optional[_Link]:
        """The issued link - the receipt's ``link``, or its ``uri`` without one - unless it is too old to hand out."""
        uri = receipt.link or receipt.uri
        renewed = file.with_access(receipt.access_expires_at, receipt.retention_expires_at)
        if not uri or renewed.access_expired(self._now(), margin=FRESH_LINK_MARGIN):
            logger.warning(
                "The new download link for stored file %s is unusable: it names no address "
                "or expires within %d s",
                file.file_id,
                FRESH_LINK_MARGIN.total_seconds(),
            )
            return None
        return _Link(uri, renewed)

    # -- remembering ------------------------------------------------------------

    def _recent_refusal(self, key: _Key, asker: _Asker) -> Optional[_Refusal]:
        """The refusal of a link to ``key`` for ``asker`` within ``RETRY_REFUSED_AFTER_SECONDS``; None without one."""
        refusals = self._refused.get(key, {})
        refusal = refusals.get(asker)
        if refusal is None:
            return None
        if time.monotonic() - refusal.at < RETRY_REFUSED_AFTER_SECONDS:
            return refusal
        del refusals[asker]
        if not refusals:
            del self._refused[key]
        return None

    def _remember_refusal(self, key: _Key, asker: _Asker, refusal: _Refusal) -> None:
        self._refused.setdefault(key, {})[asker] = refusal
        if sum(len(refusals) for refusals in self._refused.values()) > MAX_REMEMBERED:
            horizon = time.monotonic() - RETRY_REFUSED_AFTER_SECONDS
            recent = {
                k: {a: kept for a, kept in refusals.items() if kept.at >= horizon}
                for k, refusals in self._refused.items()
            }
            self._refused = {k: refusals for k, refusals in recent.items() if refusals}

    def _remember_upload(self, receipt: UploadReceipt, context: UploadContext) -> None:
        """Keep the link a file was stored with for the attribution it was stored under.

        The part keeps the file's address; the first answer that carries the
        file, and a read in the turn that stored it, hand out this link
        without asking for another.
        """
        if not receipt.link or not receipt.file_id or not receipt.version_id:
            return
        file = StoredFile.create(receipt.file_id, receipt.version_id)
        link = self._link(file, receipt)
        if link is not None:
            self._remember_link((receipt.file_id, receipt.version_id), _asker(context), link)

    def _remember_link(self, key: _Key, asker: _Asker, link: _Link) -> None:
        refusals = self._refused.get(key)
        if refusals is not None:
            refusals.pop(asker, None)
            if not refusals:
                del self._refused[key]
        self._links[(*key, asker)] = link
        if len(self._links) > MAX_REMEMBERED:
            now = self._now()
            kept = {k: v for k, v in self._links.items() if not v.file.access_expired(now)}
            while len(kept) > MAX_REMEMBERED:
                del kept[next(iter(kept))]
            self._links = kept

    # -- writing ----------------------------------------------------------------

    @staticmethod
    def _with_links(content: T, issued: dict[_Key, _Link]) -> T:
        """A copy of ``content`` whose stored file parts carry the issued links, and their expiry."""
        if not any(_key(part) in issued for part in _parts(content)):
            return content
        fresh = copy.deepcopy(content)
        for part in _parts(fresh):
            link = issued.get(_key(part))
            own = StoredFile.of(part)
            if link is None or own is None:
                continue
            part.url = link.uri
            own.with_access(link.file.access_expires_at, link.file.retention_expires_at).write_to(part)
        return fresh


def _asker(context: Optional[UploadContext]) -> _Asker:
    """On whose behalf the storage service is asked: a digest of the attribution's carrier, or None for the daemon.

    The same attribution the Files API client sends (``callback_headers``):
    the carrier ``context`` names, else that of its runtime context, else
    that of the scope the call runs in - an agent's turn.
    """
    carrier = context.usage_attribution if context is not None else None
    if carrier is None:
        runtime = context.runtime_context if context is not None else None
        runtime = runtime if runtime is not None else get_aion_runtime_context()
        try:
            attribution = runtime.get_callback_attribution() if runtime is not None else None
        except AionAuthenticationError:
            attribution = None
        carrier = attribution.carrier if attribution is not None else None
    return _digest(carrier)


def _digest(carrier: Optional[str]) -> _Asker:
    """A digest that tells carriers apart without keeping one: a carrier is a credential."""
    return hashlib.sha256(carrier.encode()).hexdigest() if carrier else None


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
