"""Utilities for detecting and handling JSX Card documents.

JSX Cards are lightweight, provider-neutral card documents defined in the
Aion Distribution/Cards extension. They use JSX-like syntax with a top-level
<Card> component.

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

import re
from a2a.types import Artifact, Part
from aion.core.constants import CARDS_EXTENSION_URI_V1, CARDS_MEDIA_TYPE, CARDS_PAYLOAD_SCHEMA_V1
from typing import Any
from uuid import uuid4

# Precompiled regex patterns (much faster than compiling on each call)
_CARD_OPEN_PATTERN = re.compile(r"<Card\b")
_CARD_CLOSE_PATTERN = re.compile(r"</Card\s*>")
_CARD_OPEN_TAG_PATTERN = re.compile(r"<Card\b(.*?)(?:/?>)", re.DOTALL)
_CARD_TITLE_ATTR = re.compile(r'\btitle=(?:"([^"]*)"|\'([^\']*)\')')
_CARD_NAME_ATTR = re.compile(r'\bname=(?:"([^"]*)"|\'([^\']*)\')')


def is_jsx_card(content: Any) -> bool:
    """
    Detect if content is a JSX Card document.

    A JSX Card is a string starting with <Card> component and properly closed
    with </Card>. Leading/trailing whitespace is tolerated.

    Args:
        content: The content to check (typically a string).

    Returns:
        True if content is a valid JSX Card document, False otherwise.

    Examples:
        >>> is_jsx_card("<Card><Text>Hello</Text></Card>")
        True
        >>> is_jsx_card("  <Card title='Test'></Card>")
        True
        >>> is_jsx_card("<Text>Not a card</Text>")
        False
    """
    if not isinstance(content, str):
        return False

    trimmed = content.strip()

    # Minimum valid Card: <Card></Card> = 12 chars
    if len(trimmed) < 12 or not trimmed.startswith("<Card"):
        return False

    # Ensure <Card is a proper tag (not <CardName>)
    if len(trimmed) > 5 and trimmed[5] not in (" ", ">", "\n", "\t", "\r"):
        return False

    # Count opening and closing tags
    open_count = sum(1 for _ in _CARD_OPEN_PATTERN.finditer(trimmed))
    close_count = sum(1 for _ in _CARD_CLOSE_PATTERN.finditer(trimmed))

    return open_count == close_count


def extract_card_name(card_jsx: str) -> str | None:
    """Extract a display name from a JSX Card document.

    Reads the title attribute first, falls back to name.
    Returns None if neither is present on the <Card> tag.
    """
    tag_match = _CARD_OPEN_TAG_PATTERN.search(card_jsx.lstrip())
    if not tag_match:
        return None

    attrs = tag_match.group(1)
    for pattern in (_CARD_TITLE_ATTR, _CARD_NAME_ATTR):
        m = pattern.search(attrs)
        if m:
            return m.group(1) if m.group(1) is not None else m.group(2)

    return None


def build_card_artifact(card_jsx: str, name: str | None = None, metadata: dict | None = None) -> Artifact:
    """Build an A2A Artifact from a JSX Card document.

    Args:
        card_jsx: The JSX Card document string.
        name: Optional artifact name. Defaults to "card".
        metadata: Optional extra metadata to merge into the card part metadata.

    Returns:
        An A2A Artifact with the card document as a file part.
    """
    card_metadata = {CARDS_EXTENSION_URI_V1: {"schema": CARDS_PAYLOAD_SCHEMA_V1}}
    if metadata:
        card_metadata.update(metadata)

    card_part = Part(
        raw=card_jsx.encode("utf-8"),
        media_type=CARDS_MEDIA_TYPE,
        metadata=card_metadata,
    )

    card_name = name or extract_card_name(card_jsx) or "Card"
    return Artifact(
        artifact_id=str(uuid4()),
        name=card_name,
        parts=[card_part],
    )
