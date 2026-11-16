"""A file the model refuses is left out, and the conversation goes on.

The message with the file stays in the framework's history, so without help
every later turn would send the model the same file and fail the same way.
The SDK's model classes - what ``aion_chat_openai`` and ``aion_lite_llm``
build - find the file the model refused and send a note in its place, from
that call on. Outside the model call nothing changes: the client sees one
ordinary turn, and the task keeps the file as it was sent.

The agent's stand-in model refuses a request that carries a file of
``REFUSED_MEDIA_TYPE``; the deployment stores files, so the model class reads
each file for the request as it does in production.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from aion.core.agent.left_out_files import RequestFile, left_out_note
from tests.scenarios.agents.native_script import REFUSED_MEDIA_TYPE
from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import REFUSED_FILES_AGENTS, Framework
from tests.scenarios.harness import (
    DISTRIBUTION_EXTENSION_URI,
    FileAttachment,
    ScenarioClient,
    ServeVariant,
    distribution_metadata,
    final_task,
    section_texts,
)
from tests.scenarios.harness.recorder import stored_messages

pytestmark = [pytest.mark.native, pytest.mark.files]

FILE_STORAGE = ServeVariant(
    name="file-storage",
    enabled_extensions=(DISTRIBUTION_EXTENSION_URI,),
    env={"FILE_STORAGE_BACKEND": "stub"},
)

PNG = FileAttachment(name="pixel.png", media_type="image/png", content=b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
CLIP = FileAttachment(name="clip.video", media_type=REFUSED_MEDIA_TYPE, content=b"clip " * 8)


@pytest_asyncio.fixture(loop_scope="session")
async def client(framework: Framework):
    """A client on the refused-files agent of a deployment that stores files."""
    server = serve_for(REFUSED_FILES_AGENTS[framework.name], FILE_STORAGE)
    async with await ScenarioClient.connect(server.base_url, server.variant.agent_id) as client:
        yield client


async def _send(client: ScenarioClient, text: str, files=(), **kwargs):
    return await client.send(
        text,
        files=list(files),
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI],
        **kwargs,
    )


NOTE = {"type": "text", "text": left_out_note(RequestFile(key="", name=CLIP.name, mime_type=CLIP.media_type))}


async def test_the_model_gets_a_note_in_place_of_the_file_it_refused(client: ScenarioClient) -> None:
    """The turn answers; the other file reaches the model, the refused one is a note."""
    events = await _send(client, "inputs", files=[PNG, CLIP])

    assert final_task(events).state == "COMPLETED"
    (answer,) = section_texts(events)
    assert json.loads(answer) == [
        {"type": "text", "text": "inputs"},
        {"type": "file", "mime_type": PNG.media_type, "size": len(PNG.content)},
        NOTE,
    ]


async def test_later_turns_of_the_conversation_answer(client: ScenarioClient) -> None:
    """The message with the file stays in the history; the next turn leaves the file out from the start."""
    first = await _send(client, "say hello", files=[CLIP])
    assert final_task(first).state == "COMPLETED"

    later = await _send(client, "say still here", context_id=final_task(first).context_id)

    assert final_task(later).state == "COMPLETED"
    assert section_texts(later) == ["still here"]


async def test_the_task_keeps_the_file_as_it_was_sent(client: ScenarioClient) -> None:
    """The note is in the model request only: the task holds the file's address, as for any file."""
    events = await _send(client, "say hello", files=[CLIP])

    stored = await client.get_task(final_task(events).task_id)

    files = [
        part for message in stored_messages(stored) for part in message.parts if part.WhichOneof("content") != "text"
    ]
    assert [(part.WhichOneof("content"), part.media_type) for part in files] == [("url", CLIP.media_type)]
    assert section_texts(events) == ["hello"]
