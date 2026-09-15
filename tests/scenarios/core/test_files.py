"""Inline file parts: stored on the way in and out, rejected, or passed through.

With a storage backend configured the server stores every inline file before
anything else sees it and hands on a URL instead; without one, bytes travel
unchanged. These scenarios pin both modes down from the outside - what the
agent saw, what came back, what a later read of the task returns.

The backend here is the SDK's stub, which hands back a URL without storing
anything: it proves the conversion path, not a storage service. The Aion
Files API backend needs platform credentials and is checked by hand.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from a2a.utils.errors import InvalidParamsError

from tests.scenarios.commands import (
    ARTIFACT_DATA_NAME,
    ARTIFACT_FILE_BYTES,
    ARTIFACT_FILE_NAME,
    ARTIFACT_URL,
    ARTIFACT_URL_NAME,
    PartsResponse,
    artifacts_text,
)
from tests.scenarios.harness import (
    DISTRIBUTION_EXTENSION_URI,
    Ev,
    FileAttachment,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    distribution_metadata,
    final_task,
    reply_texts,
)

pytestmark = pytest.mark.files

STUB_URL_PREFIX = "https://stub-storage.example.com/files/"

FILE_STORAGE = ServeVariant(
    name="file-storage",
    enabled_extensions=(DISTRIBUTION_EXTENSION_URI,),
    env={"FILE_STORAGE_BACKEND": "stub"},
)
AION_NO_CREDENTIALS = ServeVariant(
    name="aion-no-credentials",
    env={"FILE_STORAGE_BACKEND": "aion"},
    expect_healthy=False,
)

PNG = FileAttachment(
    name="probe.png",
    media_type="image/png",
    content=bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    ),
)


@pytest.fixture(scope="session")
def storage_server(server_for) -> ServeProcess:
    """The deployment that stores files."""
    return server_for(FILE_STORAGE)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def storage_client(storage_server: ServeProcess):
    """A client on the storing deployment.

    Declared with pytest-asyncio's own decorator, like ``client`` in the
    conftest: a session-scoped async fixture has to name the loop it lives in.
    """
    scenario_client = await ScenarioClient.connect(storage_server.base_url, storage_server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


def parts_seen(events: list[Ev]) -> PartsResponse:
    """What the agent reported receiving."""
    return PartsResponse.model_validate_json(reply_texts(events)[-1])


def artifact_named(events: list[Ev], name: str):
    """The parts of the one artifact update carrying this name."""
    matches = [event for event in events if event.kind == "artifact" and event.artifact_name == name]
    assert len(matches) == 1, f"expected one artifact named {name!r}, got {matches}"
    return list(matches[0].raw.artifact.parts)


# --------------------------------------------------------------------------
# Inbound
# --------------------------------------------------------------------------

@pytest.mark.variant("file-storage")
@pytest.mark.command("parts")
async def test_an_inbound_file_reaches_the_agent_as_a_url(storage_client: ScenarioClient) -> None:
    """The agent never sees the bytes: by the time it runs, the file is a URL."""
    events = await storage_client.send(
        "parts",
        files=[PNG],
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    seen = parts_seen(events)
    assert seen.kinds == ["text", "url"]
    assert seen.raw_bytes == 0
    assert len(seen.urls) == 1 and seen.urls[0].startswith(STUB_URL_PREFIX)
    assert seen.urls[0].endswith("/probe.png")
    assert final_task(events).state == "COMPLETED"


@pytest.mark.variant("file-storage")
@pytest.mark.command("parts")
async def test_the_stored_task_holds_the_url_and_not_the_bytes(storage_client: ScenarioClient) -> None:
    """Reading the task back returns what was persisted: a URL part, no raw content."""
    events = await storage_client.send(
        "parts",
        files=[PNG],
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    task = await storage_client.get_task(final_task(events).task_id, history_length=10)

    inbound = [message for message in task.history if message.role == 1]  # ROLE_USER
    assert inbound, f"history carries no user message: {task}"
    parts = list(inbound[0].parts)
    assert [part.WhichOneof("content") for part in parts] == ["text", "url"]
    assert parts[1].url.startswith(STUB_URL_PREFIX)
    assert not any(part.raw for message in task.history for part in message.parts)


@pytest.mark.variant("file-storage")
@pytest.mark.command("parts")
async def test_an_inbound_file_without_a_distribution_is_rejected(storage_client: ScenarioClient) -> None:
    """No distribution means no owning organization: the request is refused, not degraded."""
    with pytest.raises(InvalidParamsError) as error:
        await storage_client.send("parts", files=[PNG])

    assert "distribution" in str(error.value)


@pytest.mark.variant("file-storage")
@pytest.mark.command("parts")
async def test_a_distribution_without_a_principal_cannot_own_a_file(storage_client: ScenarioClient) -> None:
    """A service identity alone names nobody to own the file."""
    with pytest.raises(InvalidParamsError) as error:
        await storage_client.send(
            "parts",
            files=[PNG],
            metadata=distribution_metadata(principal=False, service=True),
            extensions=[DISTRIBUTION_EXTENSION_URI],
        )

    assert "principal identity" in str(error.value)


@pytest.mark.variant("file-storage")
@pytest.mark.command("parts")
async def test_text_only_requests_need_no_distribution(storage_client: ScenarioClient) -> None:
    """Storage is a concern of file parts only; a plain message is unaffected."""
    events = await storage_client.send("parts")

    assert parts_seen(events).kinds == ["text"]
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("parts")
async def test_without_a_backend_inline_content_passes_through(client: ScenarioClient) -> None:
    """Passthrough is a supported mode: no backend, the bytes reach the agent and the record."""
    events = await client.send("parts", files=[PNG])

    seen = parts_seen(events)
    assert seen.kinds == ["text", "raw"]
    assert seen.raw_bytes == len(PNG.content)

    task = await client.get_task(final_task(events).task_id, history_length=10)
    raw = [part.raw for message in task.history for part in message.parts if part.raw]
    assert raw == [PNG.content]


# --------------------------------------------------------------------------
# Outbound
# --------------------------------------------------------------------------

@pytest.mark.variant("file-storage")
@pytest.mark.command("artifacts")
async def test_an_outbound_file_leaves_as_a_url(storage_client: ScenarioClient) -> None:
    """The agent emits bytes; the client receives a URL. Data and url parts are untouched."""
    events = await storage_client.send(
        "artifacts",
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    (file_part,) = artifact_named(events, ARTIFACT_FILE_NAME)
    assert file_part.WhichOneof("content") == "url"
    assert file_part.url.startswith(STUB_URL_PREFIX)
    assert not file_part.raw

    data_parts = artifact_named(events, ARTIFACT_DATA_NAME)
    assert [part.WhichOneof("content") for part in data_parts] == ["data", "data"]

    (url_part,) = artifact_named(events, ARTIFACT_URL_NAME)
    assert url_part.url == ARTIFACT_URL

    assert artifacts_text() in reply_texts(events)
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("artifacts")
async def test_without_a_backend_an_outbound_file_stays_inline(client: ScenarioClient) -> None:
    """No backend, no conversion: the bytes the agent emitted are the bytes received."""
    events = await client.send("artifacts")

    (file_part,) = artifact_named(events, ARTIFACT_FILE_NAME)
    assert file_part.raw == ARTIFACT_FILE_BYTES


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

@pytest.mark.variant("aion-no-credentials")
def test_the_aion_backend_does_not_serve_without_credentials(server_for) -> None:
    """Selecting the Aion backend without AION_CLIENT_ID and AION_CLIENT_SECRET is a startup error.

    Whether the launcher gives up entirely or keeps the proxy up with an
    unhealthy agent is the launcher's business; either way the agent must not
    answer, and the reason must be in the log.
    """
    try:
        process = server_for(AION_NO_CREDENTIALS)
    except (RuntimeError, TimeoutError) as error:
        assert "AION_CLIENT_ID" in str(error)
        return

    payload = httpx.get(f"{process.base_url}/health/system/", timeout=10.0).json()
    assert payload["overall_agents_status"] != "healthy"
    assert "AION_CLIENT_ID" in process.log_tail(200)
