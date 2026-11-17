"""Who may read a stored file of a shared conversation: its owner, not every participant.

The participants of a shared context share the conversation, so one
participant's turn sees the files another sent. The storage service grants a
link to a stored file only on behalf of its owner, the usage attribution it
was stored under. Here the turn's conversation, ``StoredFileLinks`` and the
upload manager are the real ones; the storage backend grants links to the
owner only, and the download of an address is recorded instead of made.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus

import aion.core.runtime.context.conversation as conversation_module
from aion.core.a2a.files import StoredFile, files_of
from aion.core.agent.model_files import ModelFileForbiddenError, read_file_for_model
from aion.core.agent.testing import use_conversation
from aion.core.exceptions import ConversationFileError, ConversationFileForbiddenError, TaskFailedError
from aion.core.runtime.context import ForwardedAttribution, get_aion_runtime_context
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.files import links as links_module
from aion.server.files.links import StoredFileLinks
from aion.server.files.storage import (
    FileStorageBackend,
    FileUploadErrorCode,
    FileUploadManager,
    UploadContext,
    UploadFailure,
    UploadReceipt,
)
from aion.server.tasks.conversation import _StoredConversation

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
ADDRESS = "https://files.test/files/file-1/versions/version-1/content"
OWNER, OTHER = "carrier-alice", "carrier-bob"
CONTENT = b"%PDF-1.7 alice's report"


class _OwnerOnly(FileStorageBackend):
    """Grants a link to the file's owner only, on behalf of the attribution the turn runs under, as Aion Files does."""

    def __init__(self) -> None:
        self.asked_by: list[Optional[str]] = []
        self.failure: Optional[UploadFailure] = None
        self.delay = 0.0

    async def store_many(self, uploads, *, context):
        raise AssertionError("nothing is uploaded here")

    async def renew_link(self, file_id, version_id, *, context=None):
        runtime = get_aion_runtime_context()
        attribution = runtime.get_callback_attribution() if runtime is not None else None
        carrier = attribution.carrier if attribution is not None else None
        self.asked_by.append(carrier)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure is not None:
            return self.failure
        if carrier != OWNER:
            return UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
        return UploadReceipt(
            uri=ADDRESS,
            link=f"https://files.test/content?grant=for-{carrier}",
            file_id=file_id,
            version_id=version_id,
            access_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        )


class _Runs:
    """The context's runs: one task of the owner's, whose message carries the stored file."""

    def __init__(self, part: Part) -> None:
        self._task = Task(
            id="alices-task",
            context_id="shared",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            history=[Message(message_id="m-1", role=Role.ROLE_USER, parts=[part])],
        )

    async def holds(self, context_id, holder) -> bool:
        return True

    async def tasks(self, context_id, *, states=None, limit=None) -> list[Task]:
        return [self._task]


def _stored_part() -> Part:
    """A part as the Aion backend stores it: the version's protected address, and the file it names."""
    part = Part(url=ADDRESS, filename="a.pdf", media_type="application/pdf")
    StoredFile.create("file-1", "version-1", retention_expires_at=NOW + timedelta(days=30)).write_to(part)
    return part


@pytest.fixture
def backend() -> _OwnerOnly:
    return _OwnerOnly()


@pytest.fixture
def links(backend) -> StoredFileLinks:
    return StoredFileLinks(FileUploadManager(backend), now=lambda: NOW)


@pytest.fixture
def conversation(links) -> _StoredConversation:
    return _StoredConversation(
        None, _Runs(_stored_part()), None, context_id="shared", task_id="bobs-task", holder=object(), file_links=links
    )


@pytest.fixture
def fetched(monkeypatch) -> list[str]:
    """The addresses downloaded: a link answers with the file, the protected address refuses a request without one."""
    downloads: list[str] = []

    async def fetch(url: str, *, label: str, max_bytes=None) -> bytes:
        downloads.append(url)
        if "grant=" in url:
            return CONTENT
        raise ConversationFileError(
            f"File {label!r} could not be read: its address answered HTTP 401. The address refused access.",
            status_code=401,
        )

    monkeypatch.setattr(conversation_module, "fetch_file", fetch)
    return downloads


@contextmanager
def turn_of(carrier: str, conversation: _StoredConversation) -> Iterator[None]:
    """Run the block as the turn of the participant whose usage attribution is ``carrier``."""
    with use_conversation(conversation):
        current = AionRuntimeContextRegistry.get_current_context()
        AionRuntimeContextRegistry.set_current_context(
            current.replace(callback_attribution=ForwardedAttribution(carrier))
        )
        yield


class TestAnotherParticipantsFile:
    NOT_AVAILABLE = "File 'a.pdf' (application/pdf) is not available to this sender."

    async def test_it_is_neither_read_nor_linked(self, conversation, backend, fetched) -> None:
        part = _stored_part()
        file = files_of(Message(message_id="m-1", role=Role.ROLE_USER, parts=[part]))[0]

        with turn_of(OTHER, conversation):
            reads = [
                file.read(),
                file.link(),
                conversation.read_file(part),
                conversation.read_file_at(ADDRESS),
                conversation.file_link(part),
            ]
            for read in reads:
                with pytest.raises(ConversationFileForbiddenError) as refused:
                    await read
                assert str(refused.value) == self.NOT_AVAILABLE

        assert fetched == []
        # Asked once: the refusal is remembered for the participant's attribution.
        assert backend.asked_by == [OTHER]

    async def test_the_model_helper_reports_it_as_left_out(self, conversation, fetched) -> None:
        with turn_of(OTHER, conversation):
            with pytest.raises(ModelFileForbiddenError):
                await read_file_for_model(ADDRESS, name="a.pdf", stored=StoredFile.of(_stored_part()))
            with pytest.raises(ModelFileForbiddenError):
                await read_file_for_model(ADDRESS, name="a.pdf")

        assert fetched == []

    async def test_the_owner_still_reads_it(self, conversation, backend, fetched) -> None:
        part = _stored_part()
        with turn_of(OTHER, conversation), pytest.raises(ConversationFileForbiddenError):
            await conversation.read_file(part)

        with turn_of(OWNER, conversation):
            assert await files_of(Message(message_id="m-1", parts=[part]))[0].read() == CONTENT
            assert await conversation.file_link(part) == f"https://files.test/content?grant=for-{OWNER}"

        assert fetched == [f"https://files.test/content?grant=for-{OWNER}"]
        assert backend.asked_by == [OTHER, OWNER]

    async def test_an_answer_to_the_participant_keeps_the_address(self, conversation, links, fetched) -> None:
        """What leaves the server for a participant the file is refused to keeps the protected address."""
        part = _stored_part()
        with turn_of(OTHER, conversation), pytest.raises(ConversationFileForbiddenError):
            await conversation.file_link(part)

        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[part])
        answered = await links.refresh(message, context=UploadContext(usage_attribution=OTHER))

        assert answered is message
        assert answered.parts[0].url == ADDRESS


class TestAFailureThatIsNoRefusal:
    """A link that is not issued for any other reason leaves the read as it is: through the part's address."""

    @pytest.mark.parametrize("failure", [
        UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True),
        UploadFailure(FileUploadErrorCode.NO_DAEMON_IDENTITY),
    ], ids=["unavailable", "no daemon identity"])
    async def test_the_read_goes_to_the_address_and_fails(self, conversation, backend, fetched, failure) -> None:
        backend.failure = failure

        with turn_of(OTHER, conversation):
            with pytest.raises(ConversationFileError) as failed:
                await conversation.read_file(_stored_part())
            with pytest.raises(TaskFailedError) as model_failed:
                await read_file_for_model(ADDRESS, name="a.pdf", stored=StoredFile.of(_stored_part()))

        assert not isinstance(failed.value, ConversationFileForbiddenError)
        assert failed.value.status_code == 401
        assert not isinstance(model_failed.value, ModelFileForbiddenError)
        assert fetched == [ADDRESS, ADDRESS]

    async def test_a_link_that_does_not_come_in_time(self, conversation, backend, fetched, monkeypatch) -> None:
        monkeypatch.setattr(links_module, "RENEWAL_TIMEOUT_SECONDS", 0.05)
        backend.delay = 5

        with turn_of(OTHER, conversation), pytest.raises(ConversationFileError) as failed:
            await asyncio.wait_for(conversation.read_file(_stored_part()), timeout=1)

        assert not isinstance(failed.value, ConversationFileForbiddenError)
        assert fetched == [ADDRESS]
