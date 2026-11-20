"""A part whose address leads to a stored file becomes that stored file.

A framework that keeps only the URL of a file it was given - ADK's
``file_data``, a LangGraph ``source_type: url`` block, or the agent's own URL
part - sends the file on with the storage backend's content address and no
``aion:file``. A client sends back a file from an earlier answer with the
download link it was given: the content address with the grant added. The
file transformer recognises either address, keeps the content address alone
and writes the reference to the file, so the server issues every reader a
link of their own and no grant is kept. That holds for a caller's part as
for the agent's: a reference read from an address opens nothing, as a link
is issued only on behalf of whoever reads the part.

Every request goes to a mocked transport - nothing here reaches the platform.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.genai import types as genai_types

from aion.adk.authoring.transformers import convert_genai_part_to_a2a_part
from aion.api import AionFileClient
from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.runtime.context import AionRuntimeExtensions
from aion.langgraph.server.converters.lc_to_a2a import LcToA2AConverter
from aion.server.core.app.handlers.request_preprocessors import (
    FilePartPreprocessor,
    PreprocessingContext,
)
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.links import StoredFileLinks
from aion.server.files.storage import (
    AionFileStorageBackend,
    FileUploadManager,
    StubFileStorageBackend,
)

from tests.unit.support.files import upload_context

BASE_URL = "https://api.aion.test"
FILE_ID = "0b7f3c1e-2d4a-4f6b-9c8d-1e2f3a4b5c6d"
VERSION_ID = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"
ADDRESS = f"{BASE_URL}/files/{FILE_ID}/versions/{VERSION_ID}/content"
REFERENCE = StoredFile.create(FILE_ID, VERSION_ID)
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
GRANT_EXPIRES_AT = "2030-01-01T01:00:00Z"


class StaticTokenManager:
    async def get_token(self) -> str:
        return "token"


def manager(grants: list[tuple[str, str]] | None = None) -> FileUploadManager:
    """A manager over the Aion backend storing under ``BASE_URL``.

    Its storage service stores each upload as file ``file-new`` version
    ``v-new`` and grants a link to any version it is asked about, recording
    the version in ``grants``.
    """
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/files":
            return httpx.Response(200, json={"id": "file-new", "versionId": "v-new", "revision": 1})
        if request.method == "POST" and request.url.path.endswith("/grants"):
            _, _, file_id, _, version_id, _ = request.url.path.split("/")
            if grants is not None:
                grants.append((file_id, version_id))
            return httpx.Response(200, json={
                "id": file_id,
                "versionId": version_id,
                "url": f"{BASE_URL}/files/{file_id}/versions/{version_id}/content?grant=secret",
                "accessExpiresAt": GRANT_EXPIRES_AT,
                "retentionExpiresAt": None,
            })
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = AionFileClient(
        jwt_manager=StaticTokenManager(),
        base_url=BASE_URL,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    return FileUploadManager(AionFileStorageBackend(client))


def transformer() -> A2AFileTransformer:
    return A2AFileTransformer(manager())


def adk_part(url: str = ADDRESS) -> Part:
    """The part ADK's ``file_data`` becomes: the URL, its type and name, and no metadata."""
    return convert_genai_part_to_a2a_part(
        genai_types.Part(
            file_data=genai_types.FileData(file_uri=url, mime_type="application/pdf", display_name="report.pdf")
        )
    )


def langgraph_part(url: str = ADDRESS) -> Part:
    """The part a LangGraph ``source_type: url`` block becomes: the URL, its type, and no metadata."""
    return LcToA2AConverter.from_block(
        {"type": "file", "source_type": "url", "url": url, "mime_type": "application/pdf"}
    )


def agent_message(*parts: Part, role: Role = Role.ROLE_AGENT) -> Message:
    return Message(message_id="m-1", task_id="task-1", context_id="context-1", role=role, parts=list(parts))


def artifact_event(*parts: Part) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id="task-1",
        context_id="context-1",
        artifact=Artifact(artifact_id="artifact-1", name="report", parts=list(parts)),
    )


def status_event(*parts: Part) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=agent_message(*parts)),
    )


def task(*, status=(), history=(), artifact=()) -> Task:
    return Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING,
            message=agent_message(*status) if status else None,
        ),
        history=list(history),
        artifacts=[Artifact(artifact_id="artifact-1", name="report", parts=list(artifact))] if artifact else [],
    )


# Every shape an agent sends a file in, with a way to read back its part.
OUTBOUND_SHAPES = {
    "artifact-update": (artifact_event, lambda event: event.artifact.parts[0]),
    "status-update": (status_event, lambda event: event.status.message.parts[0]),
    "message": (agent_message, lambda message: message.parts[0]),
    "task-status": (lambda part: task(status=[part]), lambda t: t.status.message.parts[0]),
    "task-history": (lambda part: task(history=[agent_message(part)]), lambda t: t.history[0].parts[0]),
    "task-artifact": (lambda part: task(artifact=[part]), lambda t: t.artifacts[0].parts[0]),
}


class TestAgentPartsGetTheirReferenceBack:
    async def test_an_adk_file_part_gets_the_reference_its_address_names(self):
        part = adk_part()
        assert not part.metadata, "ADK's file_data keeps only the URL, its type and name"
        event = artifact_event(part)

        result = await transformer().transform(event)

        restored = result.event.artifact.parts[0]
        assert StoredFile.of(restored) == REFERENCE
        assert (restored.url, restored.media_type, restored.filename) == (ADDRESS, "application/pdf", "report.pdf")
        assert result.report.changed and result.report.ok and not result.report.receipts
        assert STORED_FILE_KEY not in event.artifact.parts[0].metadata, "the source event is not changed"

    async def test_a_langgraph_url_block_gets_the_reference_its_address_names(self):
        part = langgraph_part()
        assert not part.metadata, "a source_type: url block keeps only the URL and its type"

        result = await transformer().transform(status_event(part))

        restored = result.event.status.message.parts[0]
        assert StoredFile.of(restored) == REFERENCE
        assert (restored.url, restored.media_type) == (ADDRESS, "application/pdf")

    @pytest.mark.parametrize("shape", OUTBOUND_SHAPES)
    async def test_every_outbound_shape_restores_the_reference(self, shape):
        build, part_of = OUTBOUND_SHAPES[shape]

        result = await transformer().transform(build(Part(url=ADDRESS, filename="report.pdf")))

        assert StoredFile.of(part_of(result.event)) == REFERENCE

    async def test_the_reference_carries_no_access_expiry_and_no_retention(self):
        """Nothing but the ids is known from the address: it is no link, and the file's retention is not known."""
        result = await transformer().transform(artifact_event(adk_part()))

        assert result.event.artifact.parts[0].metadata[STORED_FILE_KEY] == {
            "fileId": FILE_ID,
            "fileVersionId": VERSION_ID,
        }

    async def test_a_part_with_a_reference_of_its_own_keeps_it(self):
        part = Part(url=ADDRESS)
        StoredFile.create("other-file", "other-version").write_to(part)
        event = artifact_event(part)

        result = await transformer().transform(event)

        assert result.event is event and not result.report.changed

    @pytest.mark.parametrize("query", ["grant=secret", "grant=secret&expires=1"], ids=["grant", "any-query"])
    async def test_a_download_link_becomes_the_stored_file_it_leads_to(self, query):
        """An agent that hands out ``ConversationFile.link()`` in a file part keeps no grant in the task."""
        result = await transformer().transform(artifact_event(adk_part(f"{ADDRESS}?{query}")))

        part = result.event.artifact.parts[0]
        assert (part.url, StoredFile.of(part)) == (ADDRESS, REFERENCE)

    @pytest.mark.parametrize("url", [
        pytest.param(f"{ADDRESS}#fragment", id="fragment"),
        pytest.param(f"https://files.elsewhere.test/files/{FILE_ID}/versions/{VERSION_ID}/content", id="other-host"),
        pytest.param(f"{BASE_URL}/files/{FILE_ID}/content", id="malformed-path"),
        pytest.param(f"{BASE_URL}/files/../versions/{VERSION_ID}/content", id="dot-segment"),
    ])
    async def test_a_url_that_is_no_content_address_is_left_as_it_is(self, url):
        event = artifact_event(adk_part(url))

        result = await transformer().transform(event)

        assert result.event is event and not result.report.changed
        assert STORED_FILE_KEY not in result.event.artifact.parts[0].metadata

    async def test_files_are_stored_and_references_restored_in_one_event(self):
        """Each part keeps its place: the inline file becomes its stored part, the address gets its reference."""
        event = artifact_event(
            Part(text="Here are both reports."),
            Part(raw=b"%PDF-", media_type="application/pdf", filename="new.pdf"),
            adk_part(),
        )

        result = await transformer().transform(event, upload_context=upload_context())

        text, stored, restored = result.event.artifact.parts
        assert text.text == "Here are both reports."
        assert stored.url == f"{BASE_URL}/files/file-new/versions/v-new/content"
        assert StoredFile.of(stored).file_id == "file-new"
        assert StoredFile.of(restored) == REFERENCE
        assert len(result.report.receipts) == 1

    async def test_a_backend_that_recognises_no_address_restores_nothing(self):
        event = artifact_event(adk_part())

        result = await A2AFileTransformer(FileUploadManager(StubFileStorageBackend())).transform(event)

        assert result.event is event and not result.report.changed


class TestCallersParts:
    """A caller's part has no reference of its own, but one whose address leads to a stored file becomes it."""

    @staticmethod
    async def _inbound(part: Part, role: Role = Role.ROLE_USER) -> Part:
        request = SendMessageRequest(message=Message(message_id="m-1", context_id="context-1", role=role, parts=[part]))
        context = PreprocessingContext(
            extensions=AionRuntimeExtensions({}),
            call_context=SimpleNamespace(requested_extensions=set(), state={}),
        )
        await FilePartPreprocessor(transformer()).process(request, context)
        return request.message.parts[0]

    @pytest.mark.parametrize("role", [Role.ROLE_USER, Role.ROLE_UNSPECIFIED], ids=["user", "unspecified"])
    @pytest.mark.parametrize("url", [ADDRESS, f"{ADDRESS}?grant=secret"], ids=["address", "download-link"])
    async def test_a_part_leading_to_a_stored_file_becomes_it_without_the_grant(self, role, url):
        """A client sends back a file from an earlier answer with the link it was given."""
        inbound = await self._inbound(Part(url=url, filename="report.pdf"), role)

        assert (inbound.url, StoredFile.of(inbound)) == (ADDRESS, REFERENCE)

    async def test_a_reference_of_the_callers_own_is_replaced_by_the_one_its_address_names(self):
        part = Part(url=ADDRESS, filename="report.pdf")
        StoredFile.create("other-file", "other-version").write_to(part)

        inbound = await self._inbound(part)

        assert StoredFile.of(inbound) == REFERENCE

    async def test_a_part_leading_elsewhere_keeps_its_url_and_gets_no_reference(self):
        url = "https://files.elsewhere.test/report.pdf?token=secret"

        inbound = await self._inbound(Part(url=url, filename="report.pdf"))

        assert inbound.url == url
        assert STORED_FILE_KEY not in inbound.metadata

    async def test_a_callers_message_in_an_agents_task_becomes_the_stored_file_too(self):
        event = task(
            history=[agent_message(Part(url=f"{ADDRESS}?grant=secret"), role=Role.ROLE_USER)],
            artifact=[Part(url=ADDRESS)],
        )

        result = await transformer().transform(event)

        caller = result.event.history[0].parts[0]
        assert (caller.url, StoredFile.of(caller)) == (ADDRESS, REFERENCE)
        assert StoredFile.of(result.event.artifacts[0].parts[0]) == REFERENCE


class TestRestoredPartLeavesWithALink:
    async def test_the_restored_reference_gets_a_working_link_when_handed_out(self):
        grants: list[tuple[str, str]] = []
        storage = manager(grants)
        links = StoredFileLinks(storage, now=lambda: NOW)
        event = artifact_event(adk_part())

        assert await links.refresh(event) is event, "without the reference no link is issued"
        assert grants == []

        stored = (await A2AFileTransformer(storage).transform(event)).event
        handed_out = await links.refresh(stored)

        part = handed_out.artifact.parts[0]
        assert part.url == f"{ADDRESS}?grant=secret"
        assert StoredFile.of(part).access_expires_at == datetime(2030, 1, 1, 1, tzinfo=timezone.utc)
        assert grants == [(FILE_ID, VERSION_ID)]
        assert stored.artifact.parts[0].url == ADDRESS, "what is stored keeps the address"
