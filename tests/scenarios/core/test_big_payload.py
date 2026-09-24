"""A reply large enough that nothing on the way may quietly reshape it.

``big <kb>`` answers with exactly ``kb`` * 1024 characters of deterministic
filler, so the whole answer is its own oracle: any truncation, any re-encoding
and any chunk delivered twice changes the string the client ends up with.

The size is a fixed, moderate one (``commands.BIG_KILOBYTES``). It is a
payload-integrity check, not a statement about a maximum: the SDK declares no
limit on the size of a reply, and these scenarios introduce none.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import BIG_KILOBYTES, big_text
from tests.scenarios.harness import ScenarioClient, final_task, reply_texts, stored_texts

pytestmark = [pytest.mark.errors]

EXPECTED = big_text(BIG_KILOBYTES)


@pytest.mark.command("big")
async def test_a_large_reply_arrives_whole(client: ScenarioClient) -> None:
    """One reply, of exactly the requested size, with its content intact."""
    events = await client.send(f"big {BIG_KILOBYTES}")

    replies = reply_texts(events)
    assert len(replies) == 1, f"a {BIG_KILOBYTES} KiB reply arrived in pieces: {events}"
    assert len(replies[0]) == BIG_KILOBYTES * 1024
    assert replies[0] == EXPECTED


@pytest.mark.command("big")
async def test_the_terminal_task_carries_the_whole_reply(client: ScenarioClient) -> None:
    """The Task that closes the stream is not a shortened version of it."""
    events = await client.send(f"big {BIG_KILOBYTES}")

    closing = final_task(events)

    assert closing.state == "COMPLETED"
    assert closing.text == EXPECTED


@pytest.mark.command("big")
async def test_a_large_reply_is_stored_whole(client: ScenarioClient) -> None:
    """What the server kept is what the client saw, to the last character."""
    events = await client.send(f"big {BIG_KILOBYTES}")

    stored = await client.get_task(final_task(events).task_id)

    assert EXPECTED in stored_texts(stored)
