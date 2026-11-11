"""The files of a message or an artifact, and where a stored file is kept.

``files_of`` gives the files of a message, an artifact or parts as
``ConversationFile`` objects, the way ``text_of`` and ``data_of``
(``aion.core.a2a.content``) give their text and data; a file's ``read()``
gives its bytes and ``link()`` a download link that works now.


A file the server stores reaches a task as a part with a download link in
``url``. The link gives access to whoever holds it and stops working long
before the file is removed, so the part also names the file itself: under
``aion:file`` in its metadata the server writes the file's id, the id of the
exact version, when the link stops working and when the file is removed.
Whoever holds those ids and has the right to the file can get a new link to
the same version; the server does so for the links it hands out and for the
files an agent reads (``aion.core.agent.conversation``).

The key belongs to the server: it is written when the server stores a file,
and removed from the parts of a message a caller sends.

A part with the reference, as a task holds it:

    {
      "url": "https://api.aion.to/files/<fileId>/versions/<fileVersionId>/content?grant=...",
      "filename": "report.pdf",
      "mediaType": "application/pdf",
      "metadata": {
        "aion:file": {
          "fileId": "<fileId>",
          "fileVersionId": "<fileVersionId>",
          "accessExpiresAt": "2026-11-10T13:00:00Z",
          "retentionExpiresAt": "2026-12-10T12:00:00Z"
        }
      }
    }
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from a2a.types import Artifact, Part
from google.protobuf import json_format

from aion.core.a2a.content import PartsSource, file_parts
from aion.core.a2a.enums import A2AMetadataKey

__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "STORED_FILE_KEY",
    "GENERIC_MEDIA_TYPE",
    "ConversationFile",
    "StoredFile",
    "media_type_of",
    "files_of",
    "forget_stored_file",
]

DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
"""How large a file a read takes into memory unless the caller allows more."""

STORED_FILE_KEY = A2AMetadataKey.FILE.value
"""The part metadata key the reference to a stored file is kept under."""

GENERIC_MEDIA_TYPE = "application/octet-stream"
"""The media type that says only "some bytes"; a client sends it when it does not know the file's kind."""

Timestamp = Union[datetime, str, None]


def media_type_of(part: Part) -> str:
    """The media type of a file part: the one it names, else the one its filename implies.

    ``application/octet-stream`` names no kind, so a part that carries it is
    read by its filename too - a client that does not know ``.pdf`` sends a
    PDF that way, and a model takes a file by its kind. A part that names no
    type and has no telling filename is ``application/octet-stream``.
    """
    if part.media_type and part.media_type != GENERIC_MEDIA_TYPE:
        return part.media_type
    if part.filename:
        guessed, _ = mimetypes.guess_type(part.filename)
        if guessed:
            return guessed
    return GENERIC_MEDIA_TYPE


@dataclass(frozen=True)
class StoredFile:
    """The reference a file part keeps to the file the server stored.

    Attributes:
        file_id: The file's id in the storage service.
        version_id: The id of the exact version the part's link reads.
        access_expires_at: When the part's link stops working; None when the
            storage service did not say.
        retention_expires_at: When the file is removed; None when it is kept
            indefinitely or the storage service did not say.
    """

    file_id: str
    version_id: str
    access_expires_at: Optional[datetime] = None
    retention_expires_at: Optional[datetime] = None

    @classmethod
    def create(
            cls,
            file_id: str,
            version_id: str,
            *,
            access_expires_at: Timestamp = None,
            retention_expires_at: Timestamp = None,
    ) -> "StoredFile":
        """Build a reference from ids and timestamps as a storage service reports them.

        A timestamp is a ``datetime`` or an RFC 3339 string; one that cannot
        be read is taken as not reported.
        """
        return cls(
            file_id=file_id,
            version_id=version_id,
            access_expires_at=_instant(access_expires_at),
            retention_expires_at=_instant(retention_expires_at),
        )

    @classmethod
    def of(cls, part: Part) -> Optional["StoredFile"]:
        """The reference a part carries, or None for a part without a complete one.

        Only a part with an address carries one: an inline file is in the
        part itself, and a reference without both ids names no file.
        """
        if part.WhichOneof("content") != "url" or STORED_FILE_KEY not in part.metadata:
            return None
        payload = json_format.MessageToDict(part.metadata).get(STORED_FILE_KEY)
        if not isinstance(payload, dict):
            return None
        file_id, version_id = payload.get("fileId"), payload.get("fileVersionId")
        if not isinstance(file_id, str) or not file_id or not isinstance(version_id, str) or not version_id:
            return None
        return cls.create(
            file_id,
            version_id,
            access_expires_at=payload.get("accessExpiresAt"),
            retention_expires_at=payload.get("retentionExpiresAt"),
        )

    def with_access(self, access_expires_at: Timestamp, retention_expires_at: Timestamp = None) -> "StoredFile":
        """The same file under a new link: its expiry, and the retention deadline when one is given."""
        return replace(
            self,
            access_expires_at=_instant(access_expires_at),
            retention_expires_at=_instant(retention_expires_at) or self.retention_expires_at,
        )

    def access_expired(self, now: Optional[datetime] = None, *, margin: timedelta = timedelta(0)) -> bool:
        """Whether the part's link has stopped working, or will within ``margin``.

        A reference that does not say when the link expires is taken as
        expired: nothing vouches for the link.
        """
        if self.access_expires_at is None:
            return True
        return (now or _now()) + margin >= self.access_expires_at

    def retention_expired(self, now: Optional[datetime] = None) -> bool:
        """Whether the file has been removed by its retention deadline."""
        return self.retention_expires_at is not None and (now or _now()) >= self.retention_expires_at

    def to_metadata(self) -> dict[str, Any]:
        """The reference as the value of ``aion:file``, without the fields that are not known."""
        payload: dict[str, Any] = {"fileId": self.file_id, "fileVersionId": self.version_id}
        if self.access_expires_at is not None:
            payload["accessExpiresAt"] = _wire(self.access_expires_at)
        if self.retention_expires_at is not None:
            payload["retentionExpiresAt"] = _wire(self.retention_expires_at)
        return payload

    def write_to(self, part: Part) -> None:
        """Record the reference on ``part``, replacing the one it carries."""
        part.metadata.update({STORED_FILE_KEY: self.to_metadata()})


def forget_stored_file(part: Part) -> bool:
    """Remove the reference from ``part``; whether it carried one."""
    if STORED_FILE_KEY not in part.metadata:
        return False
    del part.metadata.fields[STORED_FILE_KEY]
    if not part.metadata.fields:
        part.ClearField("metadata")
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _instant(value: Timestamp) -> Optional[datetime]:
    """A timestamp as an aware UTC ``datetime``; None when there is none or it cannot be read."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire(value: datetime) -> str:
    """An instant as an RFC 3339 UTC timestamp ending in ``Z``."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ConversationFile:
    """One file of a message or an artifact, read into memory on demand.

    A file reaches a task either inline or, once the server has stored it, as
    an address. ``read`` gives its bytes either way and keeps them, so a
    second read costs nothing and ``content`` holds them from then on. An
    inline file needs no read: its ``content`` is there from the start.

    A stored file is read through the conversation of the turn running here,
    so ``read`` of one raises ``ConversationUnavailableError`` outside a turn.

    The turn's conversation is looked up when ``read`` or ``link`` is
    called (``aion.core.runtime.context.conversation``, which is built on
    this module and so is imported there, not at the top).

    The address a stored file's part holds (``url``) is a download link with
    a limited lifetime. ``read`` does not depend on it: the server reads the
    file through a link that works now for as long as the file is stored.
    ``link`` gives that link to code that passes an address on instead of
    reading the bytes - to a model that takes a file by URL, say.

    Args:
        part: The file part: ``raw`` bytes or a ``url``.
        default_name: The name for a part that carries no ``filename``.

    Attributes:
        part: The part the file was built from.
        name: The file's name: the part's ``filename``, or ``default_name``.
        media_type: The file's MIME type; empty when the part names none.
        url: The address the part holds; None for an inline file.
    """

    def __init__(self, part: Part, *, default_name: Optional[str] = None) -> None:
        kind = part.WhichOneof("content")
        if kind not in ("raw", "url"):
            raise ValueError("A ConversationFile is built from a part that carries a file.")
        self.part = part
        self.name: str = part.filename or default_name or "file"
        self.media_type: str = part.media_type
        self.url: Optional[str] = part.url if kind == "url" else None
        self._content: Optional[bytes] = bytes(part.raw) if kind == "raw" else None

    @property
    def inline(self) -> bool:
        """Whether the part carries the file's bytes itself."""
        return self.url is None

    @property
    def content(self) -> Optional[bytes]:
        """The file's bytes once read; None for a stored file that was not read yet."""
        return self._content

    @property
    def stored(self) -> Optional[StoredFile]:
        """Where the server keeps the file: its id, its version, when its link and the file expire.

        None for an inline file and for an address the server did not store
        the file under, such as one the agent wrote itself with
        ``url_artifact``.
        """
        return StoredFile.of(self.part)

    async def link(self) -> str:
        """A download link to the file that works now.

        The address the part holds while that still works, otherwise a link
        the server issues anew for the same file. A link gives access to
        whoever holds it: pass it on, do not log it.

        Raises:
            ConversationFileError: The file is inline and has no address.
            ConversationUnavailableError: No turn is running here.
        """
        from aion.core.runtime.context.conversation import current_conversation

        return await current_conversation().file_link(self.part)

    async def read(self, *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
        """The file's bytes, downloaded on the first call and kept.

        Args:
            max_bytes: The largest file to download; None for no limit.

        Raises:
            ConversationFileError: The file could not be read (see
                ``aion.core.runtime.context.conversation.Conversation.read_file``).
            ConversationUnavailableError: The file is stored and no turn is
                running here.
        """
        if self._content is None:
            from aion.core.runtime.context.conversation import current_conversation

            self._content = await current_conversation().read_file(self.part, max_bytes=max_bytes)
        return self._content

    async def read_text(
            self, encoding: str = "utf-8", *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES
    ) -> str:
        """The file's content decoded as text; see :meth:`read`."""
        return (await self.read(max_bytes=max_bytes)).decode(encoding)

    def __repr__(self) -> str:
        state = "inline" if self.inline else ("read" if self._content is not None else "stored")
        return f"ConversationFile(name={self.name!r}, media_type={self.media_type!r}, {state})"


def files_of(content: PartsSource) -> list[ConversationFile]:
    """The files of a message, an artifact, an ``ArtifactVersion`` or parts, in order.

    A file part without a ``filename`` takes the name of the artifact it
    belongs to.
    """
    artifact = getattr(content, "artifact", content)
    default_name = artifact.name if isinstance(artifact, Artifact) and artifact.name else None
    return [ConversationFile(part, default_name=default_name) for part in file_parts(content)]
