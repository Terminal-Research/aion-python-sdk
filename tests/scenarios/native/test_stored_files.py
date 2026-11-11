"""A stored file reaches the model by its content.

On a deployment that stores files, the bytes a caller sends are uploaded
before the agent runs, and the task and the agent hold an address. A model
is sent the content all the same: the SDK reads the file for the request and
keeps what the server has just stored in memory, so nothing is downloaded
again. The storage here is the stub backend, whose addresses lead nowhere -
the content can only have come from memory.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from tests.scenarios.conftest import serve_for
from tests.scenarios.frameworks import FILE_INLINING_AGENTS, Framework
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

STUB_URL_PREFIX = "https://stub-storage.example.com/files/"

FILE_STORAGE = ServeVariant(
    name="file-storage",
    enabled_extensions=(DISTRIBUTION_EXTENSION_URI,),
    env={"FILE_STORAGE_BACKEND": "stub"},
)

PNG = FileAttachment(name="pixel.png", media_type="image/png", content=b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
PDF = FileAttachment(name="report.pdf", media_type="application/pdf", content=b"%PDF-1.7\n" + b"report " * 8)


@pytest_asyncio.fixture(loop_scope="session")
async def storing(framework: Framework):
    """A client on the file-inlining agent of a deployment that stores files."""
    server = serve_for(FILE_INLINING_AGENTS[framework.name], FILE_STORAGE)
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


def _files_the_model_received(events) -> list[dict]:
    (answer,) = section_texts(events)
    return [part for part in json.loads(answer) if part["type"] == "file"]


async def test_the_model_receives_the_bytes_of_a_file_the_server_stored(storing: ScenarioClient) -> None:
    """An image and a document, each with every byte and no address left in the request."""
    events = await _send(storing, "inputs", files=[PNG, PDF])

    assert final_task(events).state == "COMPLETED"
    assert _files_the_model_received(events) == [
        {"type": "file", "mime_type": PNG.media_type, "size": len(PNG.content)},
        {"type": "file", "mime_type": PDF.media_type, "size": len(PDF.content)},
    ]


async def test_the_task_still_holds_the_address_and_no_bytes(storing: ScenarioClient) -> None:
    """Content goes into the model request only: what is stored is the address."""
    events = await _send(storing, "inputs", files=[PDF])

    stored = await storing.get_task(final_task(events).task_id)

    file_parts = [
        part for message in stored_messages(stored) for part in message.parts if part.WhichOneof("content") != "text"
    ]
    assert [part.WhichOneof("content") for part in file_parts] == ["url"]
    assert file_parts[0].url.startswith(STUB_URL_PREFIX)
