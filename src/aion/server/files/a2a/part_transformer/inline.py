"""File content a part carries itself rather than at an address.

A part carries a file inline in one of two ways: the bytes in its ``raw``
field, or a ``data:`` URL (RFC 2397) in its ``url`` field - the same bytes
written into the address. Neither names anything that can be downloaded, so
both are stored and kept out of the task record alike, whatever the part's
media type: a JSX Card the agent sends is a file like any other.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

from a2a.types import Part

__all__ = ["InlineContent", "MalformedDataURLError", "carries_inline_content", "inline_content"]

_DATA_SCHEME = "data:"

_DEFAULT_DATA_MEDIA_TYPE = "text/plain"
"""The type of a ``data:`` URL that declares none (RFC 2397)."""


class MalformedDataURLError(ValueError):
    """A part's ``data:`` URL does not hold content that can be decoded."""


@dataclass(frozen=True)
class InlineContent:
    """The bytes a part carries and the media type its ``data:`` URL declares."""

    data: bytes
    media_type: str | None = None


def carries_inline_content(part: Part) -> bool:
    """Whether ``part`` holds file content itself: ``raw`` bytes or a ``data:`` URL."""
    return bool(part.raw) or _is_data_url(part.url)


def inline_content(part: Part) -> InlineContent:
    """The file content ``part`` carries inline.

    Raw bytes are returned as they are. A ``data:`` URL is decoded, base64
    or percent-encoded, and its media type comes without parameters;
    ``text/plain`` when it declares none.

    Raises:
        MalformedDataURLError: The ``data:`` URL has no data section or its
            base64 does not decode.
    """
    if part.raw:
        return InlineContent(bytes(part.raw))
    return _decode_data_url(part.url)


def _is_data_url(url: str) -> bool:
    return url[: len(_DATA_SCHEME)].lower() == _DATA_SCHEME


def _decode_data_url(url: str) -> InlineContent:
    header, comma, payload = url[len(_DATA_SCHEME):].partition(",")
    if not comma:
        raise MalformedDataURLError("the data: URL has no data section")

    params = [param.strip() for param in header.split(";")]
    is_base64 = params[-1].lower() == "base64"
    declared = params[0].lower()
    media_type = declared if "/" in declared else _DEFAULT_DATA_MEDIA_TYPE

    data = unquote_to_bytes(payload)
    if is_base64:
        encoded = b"".join(data.split())
        try:
            data = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)
        except (binascii.Error, ValueError) as error:
            raise MalformedDataURLError("the data: URL's base64 does not decode") from error
    return InlineContent(data, media_type)
