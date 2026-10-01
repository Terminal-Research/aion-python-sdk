"""The canonical subject a verified token names its caller by: ``aion:v1:<PrincipalType>:<encoded-id>``."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass

__all__ = [
    "ANONYMOUS_SESSION",
    "MAX_PRINCIPAL_ID_BYTES",
    "InvalidPrincipalError",
    "Principal",
    "canonical_uuid",
]

ANONYMOUS_SESSION = "AnonymousSession"
"""The principal type of a session: its ID is the session's UUID."""

MAX_PRINCIPAL_ID_BYTES = 1024
"""The longest principal ID, in UTF-8 bytes, a subject may carry."""

_PREFIX = "aion:v1:"
_TYPE = re.compile(r"[A-Z][A-Za-z0-9]*")
_ENCODED_ID = re.compile(r"[A-Za-z0-9_-]+")


class InvalidPrincipalError(ValueError):
    """A subject that is not a canonical principal."""


@dataclass(frozen=True)
class Principal:
    """Who a subject names: a principal type, and an ID that is unique within it.

    The ID is the exact UTF-8 string Aion persists - a lowercase UUID for the
    UUID-backed types, an opaque case-sensitive string for the others - and the
    subject carries it unpadded base64url, so no ID can collide with another
    type's or break the subject's delimiters.
    """

    type: str
    id: str

    def __post_init__(self) -> None:
        if not _TYPE.fullmatch(self.type):
            raise InvalidPrincipalError("the principal type is malformed")
        if not self.id:
            raise InvalidPrincipalError("the principal ID is empty")
        if len(self.id.encode("utf-8")) > MAX_PRINCIPAL_ID_BYTES:
            raise InvalidPrincipalError(f"the principal ID is longer than {MAX_PRINCIPAL_ID_BYTES} bytes")
        if self.type == ANONYMOUS_SESSION and canonical_uuid(self.id) is None:
            raise InvalidPrincipalError("an anonymous session's ID is not a lowercase UUID")

    @property
    def subject(self) -> str:
        """The canonical subject: ``aion:v1:<type>:<unpadded base64url of the ID>``."""
        encoded = base64.urlsafe_b64encode(self.id.encode("utf-8")).rstrip(b"=").decode("ascii")
        return f"{_PREFIX}{self.type}:{encoded}"

    @classmethod
    def from_subject(cls, subject: str) -> Principal:
        """The principal ``subject`` names; it has to be canonical, so it round-trips exactly.

        Raises:
            InvalidPrincipalError: ``subject`` is not a canonical principal subject.
        """
        if not subject.startswith(_PREFIX):
            raise InvalidPrincipalError("the subject is not an Aion v1 principal")
        principal_type, separator, encoded = subject[len(_PREFIX):].partition(":")
        if not separator or not _ENCODED_ID.fullmatch(encoded) or len(encoded) % 4 == 1:
            raise InvalidPrincipalError("the subject's principal ID is not unpadded base64url")
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            principal_id = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise InvalidPrincipalError("the subject's principal ID is not UTF-8 in base64url") from None
        principal = cls(type=principal_type, id=principal_id)
        if principal.subject != subject:
            raise InvalidPrincipalError("the subject is not in canonical form")
        return principal


def canonical_uuid(value: object) -> str | None:
    """``value`` when it is a UUID in canonical lowercase text, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value else None
