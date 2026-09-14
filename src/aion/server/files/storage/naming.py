"""Reduce a sender-supplied file name to a safe leaf name."""

from __future__ import annotations

import mimetypes
import posixpath
import re
from typing import Optional

__all__ = ["safe_leaf_name"]

# Everything a path separator, a control character, or a shell/URL metacharacter
# could mean somewhere downstream. Storage services differ on what they accept,
# so the name is reduced to a conservative alphabet rather than escaped.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_STEM = 96


def safe_leaf_name(
    filename: Optional[str],
    *,
    fallback_stem: str,
    media_type: Optional[str] = None,
) -> str:
    """Return a leaf name safe to present to a storage service.

    Directory components are dropped rather than escaped, and the extension is
    derived from ``media_type`` when the sender supplied none, so a stored file
    keeps a recognizable type without trusting the name it arrived under.

    Args:
        filename: Name as received. May be absent, hostile, or a full path.
        fallback_stem: Stem to use when nothing usable survives sanitizing.
            Callers pass the id they already generated for the upload, which
            keeps the stored name tied to the operation that produced it.
        media_type: MIME type used to guess an extension.

    Returns:
        A non-empty name built only from letters, digits, dot, dash and
        underscore.
    """
    leaf = posixpath.basename((filename or "").replace("\\", "/")).strip()
    stem, dot, suffix = leaf.rpartition(".")
    if not dot:
        stem, suffix = leaf, ""

    stem = _UNSAFE.sub("-", stem).strip("-.")[:_MAX_STEM] or fallback_stem
    suffix = _UNSAFE.sub("", suffix)[:16]

    if not suffix and media_type:
        guessed = mimetypes.guess_extension(media_type)
        suffix = (guessed or "").lstrip(".")

    return f"{stem}.{suffix}" if suffix else stem
