"""Assert the shape of a recorded stream.

A scenario states the sequence it expects - a status, some chunks, a message,
the terminal task - and ``assert_shape`` walks the recorded events against it.
A mismatch prints both the pattern and every event that actually arrived,
because the interesting part of a stream failure is usually the event nobody
expected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .recorder import Ev

__all__ = [
    "ANY",
    "SKIP",
    "Matcher",
    "artifact",
    "assert_shape",
    "chunks",
    "ephemeral",
    "match_shape",
    "message",
    "status",
    "task",
    "STREAM_DELTA_ARTIFACT_ID",
]

STREAM_DELTA_ARTIFACT_ID = "aion:stream-delta"
"""Artifact id the server streams a reply's text chunks under."""


def is_chunk(event: Ev) -> bool:
    """True for one streamed text chunk of a reply."""
    return event.kind == "artifact" and event.artifact_id == STREAM_DELTA_ARTIFACT_ID


@dataclass(frozen=True)
class Matcher:
    """One element of an expected shape.

    Attributes:
        label: How this element is printed in a failure.
        predicate_name: Name of the check, for the failure text.
        minimum: Fewest events this element consumes.
        maximum: Most events it consumes; None means "as many as match".
    """

    label: str
    check: object
    minimum: int = 1
    maximum: Optional[int] = 1

    def matches(self, event: Ev) -> bool:
        """Whether one event satisfies this element."""
        return bool(self.check(event))  # type: ignore[operator]

    def __repr__(self) -> str:
        return self.label


def _text_check(text: Optional[str], contains: Optional[str]):
    """A predicate over an event's text."""

    def check(event: Ev) -> bool:
        if text is not None and event.text != text:
            return False
        return not (contains is not None and contains not in event.text)

    return check


def status(
    state: Optional[str] = None,
    *,
    text: Optional[str] = None,
    contains: Optional[str] = None,
    ephemeral: Optional[bool] = None,
) -> Matcher:
    """One status update, optionally in a given state."""
    text_check = _text_check(text, contains)

    def check(event: Ev) -> bool:
        if event.kind != "status":
            return False
        if state is not None and event.state != state:
            return False
        if ephemeral is not None and event.ephemeral is not ephemeral:
            return False
        return text_check(event)

    label = "status(" + ", ".join(
        part for part in (
            state,
            f"text={text!r}" if text is not None else None,
            f"contains={contains!r}" if contains is not None else None,
            f"ephemeral={ephemeral}" if ephemeral is not None else None,
        ) if part
    ) + ")"
    return Matcher(label=label, check=check)


def ephemeral(*, contains: Optional[str] = None) -> Matcher:
    """One status update the server marked ephemeral."""
    return status(contains=contains, ephemeral=True)


def message(*, text: Optional[str] = None, contains: Optional[str] = None) -> Matcher:
    """One standalone message event."""
    text_check = _text_check(text, contains)

    def check(event: Ev) -> bool:
        return event.kind == "message" and text_check(event)

    return Matcher(label=f"message(text={text!r}, contains={contains!r})", check=check)


def task(state: Optional[str] = None, *, final: Optional[bool] = None) -> Matcher:
    """The Task object a stream opens and closes with."""

    def check(event: Ev) -> bool:
        if event.kind != "task":
            return False
        if state is not None and event.state != state:
            return False
        return not (final is not None and event.final is not final)

    return Matcher(label=f"task({state or ''}{'' if final is None else f', final={final}'})", check=check)


def artifact(
    *,
    name: Optional[str] = None,
    artifact_id: Optional[str] = None,
    append: Optional[bool] = None,
    last: Optional[bool] = None,
    contains: Optional[str] = None,
) -> Matcher:
    """One artifact update, excluding the reply's own text chunks."""
    text_check = _text_check(None, contains)

    def check(event: Ev) -> bool:
        if event.kind != "artifact" or is_chunk(event):
            return False
        if name is not None and event.artifact_name != name:
            return False
        if artifact_id is not None and event.artifact_id != artifact_id:
            return False
        if append is not None and event.append is not append:
            return False
        if last is not None and event.last_chunk is not last:
            return False
        return text_check(event)

    return Matcher(
        label=f"artifact(name={name!r}, append={append}, last={last})",
        check=check,
    )


def chunks(count: Optional[int] = None, *, at_least: Optional[int] = None) -> Matcher:
    """Streamed text chunks of one reply: exactly ``count``, or at least ``at_least``."""
    if count is None and at_least is None:
        raise ValueError("chunks() needs either a count or at_least")
    minimum = count if count is not None else at_least
    maximum = count
    label = f"chunks({count})" if count is not None else f"chunks(at_least={at_least})"
    return Matcher(label=label, check=is_chunk, minimum=int(minimum), maximum=maximum)


ANY = Matcher(label="ANY", check=lambda event: True)
"""Exactly one event, whatever it is."""

SKIP = Matcher(label="SKIP", check=lambda event: True, minimum=0, maximum=None)
"""Any number of events, including none."""


def _match(events: Sequence[Ev], index: int, pattern: Sequence[Matcher], step: int) -> bool:
    """Walk the pattern against the events, backtracking over open-ended elements."""
    if step == len(pattern):
        return index == len(events)

    matcher = pattern[step]
    available = 0
    while index + available < len(events) and matcher.matches(events[index + available]):
        available += 1
        if matcher.maximum is not None and available == matcher.maximum:
            break

    if available < matcher.minimum:
        return False

    # Longest first: an open-ended element should swallow what follows it only
    # when the rest of the pattern cannot be satisfied otherwise.
    for taken in range(available, matcher.minimum - 1, -1):
        if _match(events, index + taken, pattern, step + 1):
            return True
    return False


def match_shape(events: Sequence[Ev], pattern: Sequence[Matcher]) -> bool:
    """Whether these events fit this shape."""
    return _match(events, 0, list(pattern), 0)


def assert_shape(events: Sequence[Ev], pattern: Sequence[Matcher]) -> None:
    """Assert these events fit this shape, printing both sides when they do not."""
    if match_shape(events, pattern):
        return

    expected = "\n".join(f"  {index}. {matcher}" for index, matcher in enumerate(pattern, start=1))
    actual = "\n".join(f"  {index}. {event!r}" for index, event in enumerate(events, start=1)) or "  (none)"
    raise AssertionError(f"stream does not match the expected shape.\nexpected:\n{expected}\nactual:\n{actual}")
