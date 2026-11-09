"""The streamed answer as PostgreSQL keeps it.

The chunks of ``aion:stream-delta`` are written one by one, and nothing sends
their text again. When the task stops, the task manager joins that text into
one part before the write, so the stored task - read back from the database,
not from the server's memory - holds the answer once, in one part. The
counterpart on the default in-memory deployment is
``core/test_streaming.py``.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import stream_text
from tests.scenarios.harness import STREAM_DELTA_ARTIFACT_ID, ScenarioClient, final_task, stored_artifact

pytestmark = [pytest.mark.persistence]


@pytest.mark.command("stream")
async def test_the_stored_task_keeps_the_streamed_answer_in_one_part(pg_client: ScenarioClient) -> None:
    """Three chunks and the end mark on the wire; one part with the whole text in the database."""
    closing = final_task(await pg_client.send("stream 3"))

    stored = await pg_client.get_task(closing.task_id)

    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None, f"no streamed answer among {list(stored.artifacts)}"
    assert [part.text for part in streamed.parts] == [stream_text(3)]
