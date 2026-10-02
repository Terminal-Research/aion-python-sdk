"""The canonical subject a verified token names its caller by: ``aion:v1:<PrincipalType>:<encoded-id>``."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass

__all__ = [
    "ANONYMOUS_SESSION",
    "EXTERNAL_IDENTITY",
    "EXTERNAL_SENDER",
    "MAX_PRINCIPAL_ID_BYTES",
    "InvalidPrincipalError",
    "Principal",
    "canonical_uuid",
]

ANONYMOUS_SESSION = "AnonymousSession"
"""The principal type of a session: its ID is the session's UUID."""

EXTERNAL_IDENTITY = "ExternalIdentity"
"""The principal type of an Aion Identity record: its ID is the record's UUID."""

EXTERNAL_SENDER = "ExternalSender"
"""The principal type of a provider's sender without an Identity record.

Its ID is ``v1:<provider>:<encoded-tenant>:<encoded-sender>``: a lowercase
provider label, and the provider's tenant and sender IDs, each unpadded
base64url of its exact UTF-8. The tenant is part of who the sender is; the SDK
does not check it with the provider - Aion does, before it signs.
"""

MAX_PRINCIPAL_ID_BYTES = 1024
"""The longest principal ID, in UTF-8 bytes, a subject may carry."""

_PREFIX = "aion:v1:"
_TYPE = re.compile(r"[A-Z][A-Za-z0-9]*")
_ENCODED_ID = re.compile(r"[A-Za-z0-9_-]+")
_PROVIDER_LABEL = re.compile(r"[!-9;-~]+")
"""Printable ASCII without spaces or ``:``; that it is lowercase is checked apart."""
_UUID_TYPES = {ANONYMOUS_SESSION: "an anonymous session's", EXTERNAL_IDENTITY: "an external identity's"}


class InvalidPrincipalError(ValueError):
    """A subject that is not a canonical principal."""


@dataclass(frozen=True)
class Principal:
    """Who a subject names: a principal type, and an ID that is unique within it.

    The ID is the exact UTF-8 string Aion persists, and the subject carries
    it unpadded base64url, so no ID can collide with another type's or break
    the subject's delimiters. Every ID is non-empty and at most
    ``MAX_PRINCIPAL_ID_BYTES``; the formats the contract fixes are checked
    too - a lowercase UUID for ``AnonymousSession`` and ``ExternalIdentity``,
    the composite ``ExternalSender`` ID - and any other type's ID is taken as
    the opaque, case-sensitive string it is. The types are not a closed list
    here: which agent and system types exist, and their ID formats, is Aion's
    backend contract.
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
        if self.type in _UUID_TYPES and canonical_uuid(self.id) is None:
            raise InvalidPrincipalError(f"{_UUID_TYPES[self.type]} ID is not a lowercase UUID")
        if self.type == EXTERNAL_SENDER and not _is_external_sender_id(self.id):
            raise InvalidPrincipalError(
                "an external sender's ID is not v1:<provider>:<encoded-tenant>:<encoded-sender>"
            )

    @property
    def subject(self) -> str:
        """The canonical subject: ``aion:v1:<type>:<unpadded base64url of the ID>``."""
        return f"{_PREFIX}{self.type}:{_encoded(self.id)}"

    @classmethod
    def from_subject(cls, subject: str) -> Principal:
        """The principal ``subject`` names; it has to be canonical, so it round-trips exactly.

        Raises:
            InvalidPrincipalError: ``subject`` is not a canonical principal subject.
        """
        if not subject.startswith(_PREFIX):
            raise InvalidPrincipalError("the subject is not an Aion v1 principal")
        principal_type, separator, encoded = subject[len(_PREFIX):].partition(":")
        if not separator:
            raise InvalidPrincipalError("the subject's principal ID is not unpadded base64url")
        principal_id = _decoded(encoded)
        if principal_id is None:
            raise InvalidPrincipalError("the subject's principal ID is not UTF-8 in unpadded base64url")
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


def _encoded(value: str) -> str:
    """Unpadded base64url of ``value``'s UTF-8."""
    return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decoded(encoded: str) -> str | None:
    """The UTF-8 text ``encoded`` carries, when it is unpadded base64url in canonical form; else ``None``."""
    if not _ENCODED_ID.fullmatch(encoded) or len(encoded) % 4 == 1:
        return None
    try:
        value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return value if _encoded(value) == encoded else None


def _is_external_sender_id(value: str) -> bool:
    """Whether ``value`` is ``v1:<provider>:<encoded-tenant>:<encoded-sender>``, each part canonical."""
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "v1":
        return False
    provider, tenant, sender = parts[1:]
    if not _PROVIDER_LABEL.fullmatch(provider) or provider != provider.lower():
        return False
    return bool(_decoded(tenant)) and bool(_decoded(sender))
