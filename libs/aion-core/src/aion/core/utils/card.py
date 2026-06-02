"""Utilities for detecting and handling JSX Card documents.

JSX Cards are lightweight, provider-neutral card documents defined in the
Aion Distribution/Cards extension. They use JSX-like syntax with a top-level
<Card> component.

See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
"""

import re
from a2a.types import Part
from aion.core.agent.invocation.card import Card
from aion.core.constants import CARDS_EXTENSION_URI_V1, CARDS_MEDIA_TYPE, CARDS_PAYLOAD_SCHEMA_V1
from typing import Any

# Precompiled regex patterns (much faster than compiling on each call)
_CARD_OPEN_PATTERN = re.compile(r"<Card\b")
_CARD_CLOSE_PATTERN = re.compile(r"</Card\s*>")
_CARD_OPEN_TAG_PATTERN = re.compile(r"<Card\b(.*?)(?:/?>)", re.DOTALL)
_CARD_TITLE_ATTR = re.compile(r'\btitle=(?:"([^"]*)"|\'([^\']*)\')')


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

    Reads the ``title`` attribute from the ``<Card>`` tag.
    Returns ``None`` if the attribute is absent.
    """
    tag_match = _CARD_OPEN_TAG_PATTERN.search(card_jsx.lstrip())
    if not tag_match:
        return None

    m = _CARD_TITLE_ATTR.search(tag_match.group(1))
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)

    return None


def build_card_a2a_part(card: Card, metadata: dict | None = None) -> Part:
    """Build an A2A Part from a Card.

    Args:
        card: Card instance with either jsx or url set.
        metadata: Optional extra metadata to merge into the part metadata.

    Returns:
        An A2A Part carrying the card document as a file part.
    """
    card_metadata = {CARDS_EXTENSION_URI_V1: {"schema": CARDS_PAYLOAD_SCHEMA_V1}}
    if metadata:
        card_metadata.update(metadata)

    if card.url:
        return Part(
            url=card.url,
            media_type=CARDS_MEDIA_TYPE,
            metadata=card_metadata,
        )

    card_title = extract_card_name(card.jsx)
    filename = f"{card_title.strip().lower().replace(' ', '-')}.card.jsx" if card_title else "card.jsx"
    return Part(
        raw=card.jsx.encode("utf-8"),
        filename=filename,
        media_type=CARDS_MEDIA_TYPE,
        metadata=card_metadata,
    )
