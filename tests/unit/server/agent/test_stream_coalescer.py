"""What `StreamDeltaCoalescer` lets through, and when.

Each chunk of the streamed answer that leaves the pipeline is a write of the
task and a push to every subscriber. The coalescer lets one chunk out per
interval and joins the ones in between into it, without ever reordering the
turn or losing text. A hand-driven clock stands in for time.
"""

from __future__ import annotations

from a2a.types import Part, TaskState, TaskStatus, TaskStatusUpdateEvent

from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_final, thinking_delta
from aion.server.agent.execution.stream_coalescer import StreamDeltaCoalescer

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
INTERVAL = 0.25


class Clock:
    """Monotonic time that moves only when a test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _coalescer(interval: float = INTERVAL) -> tuple[StreamDeltaCoalescer, Clock]:
    clock = Clock()
    return StreamDeltaCoalescer(interval, clock=clock), clock


def _chunk(*parts: Part | str, append: bool = True, metadata=None):
    return stream_delta_chunk(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        parts=[Part(text=part) if isinstance(part, str) else part for part in parts],
        append=append,
        metadata=metadata,
    )


def _status() -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def _texts(event) -> list[str]:
    return [part.text for part in event.artifact.parts]


def test_the_first_chunk_goes_out_at_once() -> None:
    coalescer, _ = _coalescer()
    first = _chunk("Hel", append=False)

    (sent,) = coalescer.take(first)

    assert _texts(sent) == ["Hel"]
    assert sent.append is False


def test_chunks_within_the_interval_wait_and_are_joined_text_onto_text() -> None:
    """The chunks that came too soon go out as one chunk, their text in one part."""
    coalescer, clock = _coalescer()
    coalescer.take(_chunk("Hel", append=False))

    clock.advance(INTERVAL / 4)
    assert coalescer.take(_chunk("lo")) == []
    clock.advance(INTERVAL / 4)
    assert coalescer.take(_chunk(", wor")) == []
    clock.advance(INTERVAL / 2)
    (joined,) = coalescer.take(_chunk("ld"))

    assert _texts(joined) == ["lo, world"]
    assert joined.append is True
    assert joined.last_chunk is False


def test_a_chunk_after_the_interval_goes_out_at_once() -> None:
    coalescer, clock = _coalescer()
    coalescer.take(_chunk("Hel", append=False))

    clock.advance(INTERVAL)
    (sent,) = coalescer.take(_chunk("lo"))

    assert _texts(sent) == ["lo"]


def test_a_part_that_is_not_text_is_kept_as_a_part_of_its_own() -> None:
    """Only text joins text; anything else keeps its place among the parts."""
    coalescer, clock = _coalescer()
    coalescer.take(_chunk("a", append=False))
    data = Part(url="https://files.example/chart.png", filename="chart.png")

    coalescer.take(_chunk("b"))
    coalescer.take(_chunk(data))
    coalescer.take(_chunk("c"))
    coalescer.take(_chunk("d"))
    (joined,) = coalescer.drain()

    assert [part.WhichOneof("content") for part in joined.artifact.parts] == ["text", "url", "text"]
    assert joined.artifact.parts[0].text == "b"
    assert joined.artifact.parts[1].url == data.url
    assert joined.artifact.parts[2].text == "cd"


def test_any_other_event_sends_the_waiting_chunk_ahead_of_itself() -> None:
    """The order of the turn is kept: nothing overtakes text that came before it."""
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("Hel", append=False))
    coalescer.take(_chunk("lo"))
    status = _status()

    waiting, after = coalescer.take(status)

    assert _texts(waiting) == ["lo"]
    assert after is status


def test_a_thought_is_never_held_back() -> None:
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("Hel", append=False))
    coalescer.take(_chunk("lo"))
    thought = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="hm")

    waiting, after = coalescer.take(thought)

    assert _texts(waiting) == ["lo"]
    assert after is thought
    assert coalescer.take(thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="hm")) != []


def test_the_first_chunk_of_a_section_goes_out_at_once_in_place_of_the_waiting_ones() -> None:
    """A chunk without ``append`` replaces the artifact, so the chunks before it would be lost anyway."""
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("one", append=False))
    coalescer.take(_chunk(" and more"))

    (started,) = coalescer.take(_chunk("two", append=False))
    coalescer.take(_chunk(" again"))
    (waiting,) = coalescer.drain()

    assert (_texts(started), started.append) == (["two"], False)
    assert (_texts(waiting), waiting.append) == ([" again"], True)


def test_a_whole_section_drops_the_chunks_waiting_for_it() -> None:
    """The section's whole text replaces its chunks, so those still waiting are not sent."""
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("Hel", append=False))
    coalescer.take(_chunk("lo"))
    final = stream_delta_final(task_id=TASK_ID, context_id=CONTEXT_ID, text="Hello")

    assert coalescer.take(final) == [final]
    assert coalescer.drain() == []


def test_interval_zero_lets_every_chunk_through() -> None:
    coalescer, _ = _coalescer(interval=0)

    sent = [event for text in ("a", "b", "c") for event in coalescer.take(_chunk(text))]

    assert [_texts(event) for event in sent] == [["a"], ["b"], ["c"]]


def test_drain_hands_over_what_waits_once() -> None:
    coalescer, _ = _coalescer()
    assert coalescer.drain() == []

    coalescer.take(_chunk("Hel", append=False))
    coalescer.take(_chunk("lo"))

    (waiting,) = coalescer.drain()
    assert _texts(waiting) == ["lo"]
    assert coalescer.drain() == []


def test_a_drained_chunk_starts_the_interval_again() -> None:
    """A chunk sent with another event counts as sent: the next one waits its interval."""
    coalescer, clock = _coalescer()
    coalescer.take(_chunk("a", append=False))
    clock.advance(INTERVAL / 2)
    coalescer.take(_chunk("b"))
    coalescer.drain()

    clock.advance(INTERVAL / 2)
    assert coalescer.take(_chunk("c")) == []


def test_what_waits_is_the_coalescers_own_copy() -> None:
    """A producer that reuses or changes its chunk afterwards does not change what goes out."""
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("Hel", append=False))
    chunk = _chunk("lo")

    coalescer.take(chunk)
    chunk.artifact.parts[0].text = "changed"
    coalescer.take(_chunk("!"))

    (waiting,) = coalescer.drain()
    assert _texts(waiting) == ["lo!"]
    assert chunk.artifact.parts[0].text == "changed"


def test_a_later_chunks_metadata_is_kept_on_the_joined_chunk() -> None:
    coalescer, _ = _coalescer()
    coalescer.take(_chunk("Hel", append=False))

    coalescer.take(_chunk("lo", metadata={"source": "first"}))
    coalescer.take(_chunk("!", metadata={"source": "second", "tokens": 2}))
    (waiting,) = coalescer.drain()

    assert waiting.artifact.metadata["source"] == "second"
    assert waiting.artifact.metadata["tokens"] == 2
