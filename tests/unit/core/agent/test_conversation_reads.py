"""What ``aion.core.agent.conversation`` reads, over the conversation a test installs.

``aion.core.agent.testing`` gives agent code a conversation without a server;
the reads here are the ones every ``Conversation`` builds on its four
store reads, so they hold for the server's history too.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from a2a.types import Artifact, Message, Part, Role, TaskState

from aion.core.a2a import (
    UNFINISHED_TASK_STATES,
    data_artifact,
    file_artifact,
    files_of,
    text_artifact,
    text_of,
    url_artifact,
)
from aion.core.a2a.enums import ArtifactId
from aion.core.agent import conversation
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.a2a.files import ConversationFile
from aion.core.runtime.context.conversation import fetch_file

PDF_URL = "https://files.test/report.pdf"
CREATED_AT = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def talk() -> InMemoryConversation:
    """Two finished tasks and one waiting for input, with artifacts of each kind."""
    talk = InMemoryConversation(files={PDF_URL: b"%PDF-1.7"}, created_at=CREATED_AT)
    talk.add_task(
        task_id="first",
        messages=[(Role.ROLE_USER, "Write a report"), (Role.ROLE_AGENT, "Here is the draft.")],
        artifacts=[
            text_artifact("Draft v1", name="report.md", artifact_id="report"),
            Artifact(artifact_id=ArtifactId.STREAM_DELTA.value, parts=[Part(text="Here is the draft.")]),
        ],
    )
    talk.add_task(
        task_id="second",
        messages=["Add numbers", (Role.ROLE_AGENT, "Added.")],
        artifacts=[
            text_artifact("Draft v2", name="report.md", artifact_id="report"),
            data_artifact({"rows": 3}, name="stats", artifact_id="stats"),
            url_artifact(PDF_URL, mime_type="application/pdf", name="report.pdf", artifact_id="pdf"),
        ],
    )
    talk.add_task(
        task_id="third",
        messages=["Publish it", (Role.ROLE_AGENT, "Where to?")],
        state=TaskState.TASK_STATE_INPUT_REQUIRED,
    )
    return talk


def texts(messages: list[Message]) -> list[str]:
    return [text_of(message) for message in messages]


# -- availability ------------------------------------------------------------------


async def test_outside_a_turn_the_conversation_is_not_available() -> None:
    assert not conversation.is_available()
    with pytest.raises(conversation.ConversationUnavailableError):
        await conversation.list_messages()
    with pytest.raises(conversation.ConversationUnavailableError):
        conversation.current()


async def test_a_test_installs_a_conversation_for_a_block(talk) -> None:
    with use_conversation(talk) as installed:
        assert installed is talk
        assert conversation.is_available()
        assert conversation.current() is talk
        assert await talk.get_created_at() == CREATED_AT

    assert not conversation.is_available()


async def test_blocks_nest_and_restore_the_conversation_they_replaced(talk) -> None:
    other = InMemoryConversation(context_id="other")
    with use_conversation(talk):
        with use_conversation(other):
            assert conversation.current() is other
        assert conversation.current() is talk


# -- messages ----------------------------------------------------------------------


async def test_messages_come_oldest_first_across_the_tasks(talk) -> None:
    with use_conversation(talk):
        assert texts(await conversation.list_messages()) == [
            "Write a report", "Here is the draft.", "Add numbers", "Added.", "Publish it", "Where to?",
        ]


async def test_messages_are_narrowed_by_role_before_they_are_counted(talk) -> None:
    with use_conversation(talk):
        assert texts(await conversation.list_messages(role=Role.ROLE_USER, limit=2)) == ["Add numbers", "Publish it"]
        assert texts(await conversation.list_messages(role=Role.ROLE_AGENT, limit=1)) == ["Where to?"]


async def test_a_limit_and_an_offset_page_backwards_from_the_newest(talk) -> None:
    with use_conversation(talk):
        assert texts(await conversation.list_messages(limit=2)) == ["Publish it", "Where to?"]
        assert texts(await conversation.list_messages(limit=2, offset=2)) == ["Add numbers", "Added."]
        assert await conversation.list_messages(limit=2, offset=10) == []
        assert await conversation.list_messages(limit=0) == []


async def test_the_messages_of_one_task_are_read_by_its_id(talk) -> None:
    with use_conversation(talk):
        assert texts(await conversation.list_messages(task_id="second")) == ["Add numbers", "Added."]
        assert await conversation.list_messages(task_id="missing") == []


@pytest.mark.parametrize("arguments", [
    {"role": "user"},
    {"role": Role.ROLE_UNSPECIFIED},
    {"limit": -1},
    {"offset": -1},
])
async def test_a_filter_that_names_nothing_is_refused(talk, arguments) -> None:
    with use_conversation(talk):
        with pytest.raises(ValueError):
            await conversation.list_messages(**arguments)


# -- tasks -------------------------------------------------------------------------


async def test_tasks_are_narrowed_by_state_and_counted_from_the_newest(talk) -> None:
    with use_conversation(talk):
        ids = lambda tasks: [task.id for task in tasks]  # noqa: E731

        assert ids(await conversation.list_tasks()) == ["first", "second", "third"]
        assert ids(await conversation.list_tasks(states=[TaskState.TASK_STATE_COMPLETED])) == ["first", "second"]
        assert ids(await conversation.list_tasks(states=UNFINISHED_TASK_STATES)) == ["third"]
        assert ids(await conversation.list_tasks(limit=2)) == ["second", "third"]
        assert ids(await conversation.list_tasks(states=[TaskState.TASK_STATE_COMPLETED], limit=1)) == ["second"]


def test_a_test_task_takes_a_message_of_a_protocol_role_only() -> None:
    with pytest.raises(ValueError):
        InMemoryConversation().add_task(messages=[("agent", "Here is the draft.")])


# -- artifacts ---------------------------------------------------------------------


async def test_the_artifacts_are_the_latest_version_of_each_newest_first(talk) -> None:
    with use_conversation(talk):
        found = await conversation.list_artifacts()

    assert [(version.artifact_id, version.task_id) for version in found] == [
        ("pdf", "second"), ("stats", "second"), ("report", "second"),
    ]


async def test_every_version_and_the_sdks_own_artifacts_are_there_on_request(talk) -> None:
    with use_conversation(talk):
        versions = await conversation.list_artifacts(name="report.md", all_versions=True)
        with_sdk = await conversation.list_artifacts(include_sdk=True)
        of_first = await conversation.list_artifacts(task_id="first")

    assert [version.text for version in versions] == ["Draft v2", "Draft v1"]
    assert ArtifactId.STREAM_DELTA.value in [version.artifact_id for version in with_sdk]
    assert [version.artifact_id for version in of_first] == ["report"]


async def test_an_artifact_is_read_by_its_id_and_searched_by_its_name(talk) -> None:
    with use_conversation(talk):
        by_id = await conversation.get_artifact("report")
        (by_name,) = await conversation.list_artifacts(name="report.md", limit=1)

        assert by_id.text == by_name.text == "Draft v2"
        assert await conversation.get_artifact("nothing") is None
        assert await conversation.list_artifacts(name="nothing") == []


async def test_the_newest_artifacts_are_counted_by_a_limit(talk) -> None:
    with use_conversation(talk):
        assert [found.artifact_id for found in await conversation.list_artifacts(limit=2)] == ["pdf", "stats"]
        assert await conversation.list_artifacts(limit=0) == []
        with pytest.raises(ValueError):
            await conversation.list_artifacts(limit=-1)


def numbered(text: str, number: int) -> Artifact:
    artifact = text_artifact(text, name="spec.md", artifact_id="spec")
    artifact.metadata["version"] = str(number)
    return artifact


async def test_one_version_of_an_artifact_is_read_by_its_number() -> None:
    talk = InMemoryConversation()
    talk.add_task(task_id="first", artifacts=[numbered("Plan 0", 0)])
    talk.add_task(task_id="second", artifacts=[numbered("Plan 1", 1)])

    with use_conversation(talk):
        assert (await conversation.get_artifact("spec")).text == "Plan 1"
        assert (await conversation.get_artifact("spec", version=0)).text == "Plan 0"
        assert (await conversation.get_artifact("spec", version=1)).task_id == "second"
        assert await conversation.get_artifact("spec", version=2) is None


async def test_a_number_several_tasks_hold_gives_the_newest_of_them(talk) -> None:
    # The fixture's "report" carries no number, so it reads as version 0 in both tasks.
    with use_conversation(talk):
        assert (await conversation.get_artifact("report", version=0)).task_id == "second"


async def test_a_version_gives_its_content_by_kind(talk) -> None:
    with use_conversation(talk):
        stats = await conversation.get_artifact("stats")
        report = await conversation.get_artifact("report")

        assert (stats.name, stats.data, stats.text, stats.files) == ("stats", {"rows": 3.0}, "", ())
        assert (report.text, await report.read_text(), await report.read()) == ("Draft v2", "Draft v2", b"Draft v2")
        with pytest.raises(conversation.ConversationFileError):
            await stats.read()


# -- files -------------------------------------------------------------------------


async def test_a_stored_file_is_read_once_and_kept(talk) -> None:
    with use_conversation(talk):
        pdf = await conversation.get_artifact("pdf")
        (stored,) = pdf.files

        assert (stored.name, stored.media_type, stored.url, stored.inline, stored.content) == (
            "report.pdf", "application/pdf", PDF_URL, False, None,
        )
        assert await pdf.read() == b"%PDF-1.7"

    # Read once, the content stays with the file: no conversation is needed again.
    assert stored.content == b"%PDF-1.7"
    assert await stored.read() == b"%PDF-1.7"
    assert pdf.files[0] is stored


async def test_an_inline_file_needs_no_turn_to_be_read() -> None:
    artifact = file_artifact("привет".encode(), mime_type="text/plain", name="note.txt")
    (inline,) = files_of(artifact)

    assert (inline.name, inline.inline, inline.content) == ("note.txt", True, "привет".encode())
    assert await inline.read_text() == "привет"


async def test_a_stored_file_is_not_read_outside_a_turn() -> None:
    (stored,) = files_of(url_artifact(PDF_URL, mime_type="application/pdf"))

    with pytest.raises(conversation.ConversationUnavailableError):
        await stored.read()


async def test_the_files_of_a_message_are_read_through_the_conversation(talk) -> None:
    said = Message(
        message_id="m",
        role=Role.ROLE_USER,
        parts=[Part(text="See attached"), Part(url=PDF_URL, filename="report.pdf", media_type="application/pdf")],
    )
    with use_conversation(talk):
        (attached,) = files_of(said)

        assert await attached.read() == b"%PDF-1.7"
        assert await talk.read_file(said.parts[1]) == b"%PDF-1.7"
        with pytest.raises(conversation.ConversationFileError):
            await talk.read_file(said.parts[0])
        with pytest.raises(conversation.ConversationFileError):
            await ConversationFile(Part(url="https://files.test/unknown")).read()
        with pytest.raises(conversation.ConversationFileError):
            await attached_again(said).read(max_bytes=3)


def attached_again(said: Message) -> ConversationFile:
    return files_of(said)[0]


def test_a_file_is_built_only_from_a_part_that_carries_one() -> None:
    with pytest.raises(ValueError):
        ConversationFile(Part(text="not a file"))


# -- downloading -------------------------------------------------------------------


@pytest.fixture
def addresses(monkeypatch):
    """Answer every download from a table of address -> response, with no network."""
    table: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return table.get(str(request.url), httpx.Response(404))

    client = httpx.AsyncClient

    def mocked(**kwargs):
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    return table


async def test_a_download_follows_a_redirect_to_the_content(addresses) -> None:
    addresses["https://api.test/grant"] = httpx.Response(302, headers={"location": "https://blob.test/content"})
    addresses["https://blob.test/content"] = httpx.Response(200, content=b"content")

    assert await fetch_file("https://api.test/grant", label="a.bin") == b"content"


@pytest.mark.parametrize("status, expired", [(403, True), (410, True), (500, False)])
async def test_a_refused_download_names_the_file_never_its_address(addresses, status, expired) -> None:
    addresses["https://api.test/secret-grant"] = httpx.Response(status)

    with pytest.raises(conversation.ConversationFileError) as refused:
        await fetch_file("https://api.test/secret-grant", label="a.bin")

    assert refused.value.status_code == status
    assert "a.bin" in str(refused.value) and "secret-grant" not in str(refused.value)
    assert ("expired" in str(refused.value)) is expired


async def test_a_download_stops_at_the_size_the_caller_allows(addresses) -> None:
    addresses["https://api.test/big"] = httpx.Response(200, content=b"x" * 10)

    with pytest.raises(conversation.ConversationFileError):
        await fetch_file("https://api.test/big", label="big.bin", max_bytes=9)
    assert await fetch_file("https://api.test/big", label="big.bin", max_bytes=None) == b"x" * 10


async def test_only_http_addresses_are_downloaded() -> None:
    with pytest.raises(conversation.ConversationFileError):
        await fetch_file("file:///etc/passwd", label="passwd")


async def test_the_default_read_of_a_stored_part_downloads_it(addresses) -> None:
    class Bare(InMemoryConversation):
        read_file = conversation.Conversation.read_file

    addresses[PDF_URL] = httpx.Response(200, content=b"%PDF")
    with use_conversation(Bare()) as bare:
        assert await ConversationFile(Part(url=PDF_URL, filename="report.pdf")).read() == b"%PDF"
        assert await bare.read_file(Part(raw=b"inline")) == b"inline"


# -- stored files and their links ---------------------------------------------------


def stored_file_part() -> Part:
    part = Part(url="https://api.test/stored-link", filename="report.pdf")
    part.metadata.update({"aion:file": {
        "fileId": "file-1",
        "fileVersionId": "version-2",
        "accessExpiresAt": "2026-11-10T13:00:00Z",
        "retentionExpiresAt": "2026-12-10T12:00:00Z",
    }})
    return part


def test_a_file_says_where_the_server_keeps_it() -> None:
    stored = ConversationFile(stored_file_part()).stored

    assert (stored.file_id, stored.version_id) == ("file-1", "version-2")
    assert stored.access_expires_at.isoformat() == "2026-11-10T13:00:00+00:00"
    assert stored.retention_expires_at.isoformat() == "2026-12-10T12:00:00+00:00"


def test_an_inline_file_and_a_plain_address_are_kept_nowhere() -> None:
    assert ConversationFile(Part(raw=b"x", filename="a.bin")).stored is None
    assert ConversationFile(Part(url="https://example.test/a.pdf")).stored is None


async def test_a_files_link_is_the_one_its_conversation_gives(talk) -> None:
    file = ConversationFile(stored_file_part())

    with use_conversation(talk):
        assert await file.link() == "https://api.test/stored-link"
        assert await talk.file_link(file.part) == "https://api.test/stored-link"


async def test_an_inline_file_has_no_link(talk) -> None:
    with use_conversation(talk), pytest.raises(conversation.ConversationFileError):
        await ConversationFile(Part(raw=b"x", filename="a.bin")).link()


async def test_a_stored_file_is_read_through_the_link_the_conversation_gives(addresses) -> None:
    """A conversation that issues a new link reads the file through it, not through the expired one."""
    addresses["https://api.test/new-link"] = httpx.Response(200, content=b"content")

    class Renewing(InMemoryConversation):
        async def file_link(self, part: Part) -> str:
            return "https://api.test/new-link"

        read_file = conversation.Conversation.read_file

    file = ConversationFile(stored_file_part())
    with use_conversation(Renewing()):
        assert await file.read() == b"content"
        assert await file.link() == "https://api.test/new-link"
