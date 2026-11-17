"""Working download links for stored files: issued again, reused, and never at the stored task's cost."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)

from aion.core.a2a.files import StoredFile
from aion.server.files import links as links_module
from aion.server.files.links import StoredFileLinks
from aion.server.files.storage import (
    FileStorageBackend,
    FileUploadErrorCode,
    FileUploadManager,
    UploadFailure,
    UploadReceipt,
)

NOON = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
STORED_LINK = "https://files.test/content?grant=stored"


class Clock:
    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class LinkBackend(FileStorageBackend):
    """A backend that issues the links it is scripted to, and records what it was asked for."""

    def __init__(self, clock: Clock, *, lifetime: timedelta = timedelta(hours=1)) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self.asked: list[tuple[str, str]] = []
        self.answer = None
        self.delay = 0.0

    async def store_many(self, uploads, *, context):
        raise AssertionError("nothing is uploaded here")

    async def renew_link(self, file_id, version_id, *, context=None):
        self.asked.append((file_id, version_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        return UploadReceipt(
            uri=f"https://files.test/content?grant=new-{len(self.asked)}",
            file_id=file_id,
            version_id=version_id,
            access_expires_at=(self._clock() + self._lifetime).isoformat(),
        )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def backend(clock) -> LinkBackend:
    return LinkBackend(clock)


@pytest.fixture
def links(backend, clock) -> StoredFileLinks:
    return StoredFileLinks(FileUploadManager(backend), now=clock)


def stored_part(file_id="file-1", *, expires_in=timedelta(hours=1), retained_for=timedelta(days=30)) -> Part:
    part = Part(url=STORED_LINK, filename="report.pdf", media_type="application/pdf")
    StoredFile.create(
        file_id,
        "version-1",
        access_expires_at=NOON + expires_in,
        retention_expires_at=None if retained_for is None else NOON + retained_for,
    ).write_to(part)
    return part


def task_with(*parts: Part) -> Task:
    return Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(artifact_id="report", name="report.pdf", parts=list(parts))],
    )


async def test_a_task_whose_links_still_work_is_answered_as_it_is(links, backend) -> None:
    task = task_with(stored_part(expires_in=timedelta(minutes=30)))

    assert await links.refresh(task) is task
    assert backend.asked == []


async def test_an_expired_link_is_replaced_in_a_copy(links, backend, clock) -> None:
    task = task_with(stored_part())
    clock.advance(hours=2)

    fresh = await links.refresh(task)

    part = fresh.artifacts[0].parts[0]
    assert part.url == "https://files.test/content?grant=new-1"
    assert StoredFile.of(part).access_expires_at == clock.now + timedelta(hours=1)
    assert (part.filename, part.media_type) == ("report.pdf", "application/pdf")
    assert backend.asked == [("file-1", "version-1")]
    # The stored task keeps the link and the expiry it was written with.
    assert task.artifacts[0].parts[0].url == STORED_LINK
    assert StoredFile.of(task.artifacts[0].parts[0]).access_expires_at == NOON + timedelta(hours=1)


async def test_a_link_about_to_expire_is_replaced_too(links, backend, clock) -> None:
    task = task_with(stored_part())
    clock.advance(minutes=56)

    fresh = await links.refresh(task)

    assert fresh.artifacts[0].parts[0].url != STORED_LINK


async def test_one_link_serves_every_part_and_every_answer_of_the_same_file(links, backend, clock) -> None:
    first, second = task_with(stored_part(), stored_part()), task_with(stored_part())
    clock.advance(hours=2)

    answered = await links.refresh_many([first, second])
    again = await links.refresh(first)

    urls = {part.url for task in (*answered, again) for part in task.artifacts[0].parts}
    assert urls == {"https://files.test/content?grant=new-1"}
    assert backend.asked == [("file-1", "version-1")]


async def test_a_remembered_link_is_replaced_before_it_expires(links, backend, clock) -> None:
    task = task_with(stored_part())
    clock.advance(hours=2)
    await links.refresh(task)

    clock.advance(minutes=56)
    fresh = await links.refresh(task)

    assert fresh.artifacts[0].parts[0].url == "https://files.test/content?grant=new-2"


async def test_each_file_gets_its_own_link(links, backend, clock) -> None:
    task = task_with(stored_part("file-1"), stored_part("file-2"))
    clock.advance(hours=2)

    fresh = await links.refresh(task)

    assert len({part.url for part in fresh.artifacts[0].parts}) == 2
    assert sorted(backend.asked) == [("file-1", "version-1"), ("file-2", "version-1")]


async def test_a_refusal_leaves_the_stored_link_and_is_not_repeated(links, backend, clock, caplog, monkeypatch) -> None:
    backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
    task = task_with(stored_part())
    clock.advance(hours=2)

    with caplog.at_level(logging.WARNING, logger=links_module.__name__):
        assert await links.refresh(task) is task
        assert await links.refresh(task) is task

    assert backend.asked == [("file-1", "version-1")]
    assert "file-1" in caplog.text and "STORAGE_FORBIDDEN" in caplog.text
    assert "grant" not in caplog.text

    # Once the refusal is old enough the file is asked about again.
    monkeypatch.setattr(links_module, "RETRY_REFUSED_AFTER_SECONDS", 0.0)
    backend.answer = None
    assert (await links.refresh(task)).artifacts[0].parts[0].url != STORED_LINK


async def test_a_refusal_to_the_daemon_does_not_keep_a_caller_from_asking(links, backend, clock) -> None:
    """Aion Files grants a caller's request what it refuses the deployment's daemon."""
    from aion.server.files.storage import UploadContext

    caller = UploadContext(usage_attribution="carrier-1")
    task = task_with(stored_part())
    clock.advance(hours=2)

    backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
    assert await links.refresh(task) is task

    backend.answer = None
    fresh = await links.refresh(task, context=caller)

    assert fresh.artifacts[0].parts[0].url != STORED_LINK
    assert backend.asked == [("file-1", "version-1"), ("file-1", "version-1")]


async def test_a_refusal_is_remembered_for_the_attribution_it_was_given_to(links, backend, clock) -> None:
    """A caller who is not the file's owner is refused; the owner asking after them is not kept waiting."""
    from aion.server.files.storage import UploadContext

    task = task_with(stored_part())
    clock.advance(hours=2)
    backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)

    assert await links.refresh(task, context=UploadContext(usage_attribution="carrier-1")) is task
    assert await links.refresh(task, context=UploadContext(usage_attribution="carrier-1")) is task
    assert backend.asked == [("file-1", "version-1")]

    backend.answer = None
    fresh = await links.refresh(task, context=UploadContext(usage_attribution="carrier-2"))

    assert fresh.artifacts[0].parts[0].url != STORED_LINK
    assert backend.asked == [("file-1", "version-1"), ("file-1", "version-1")]


async def test_a_refusal_is_remembered_without_its_carrier(links, backend, clock) -> None:
    """A carrier is a credential: what is kept about a refusal cannot be used as one."""
    from aion.server.files.storage import UploadContext

    clock.advance(hours=2)
    backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)

    await links.refresh(task_with(stored_part()), context=UploadContext(usage_attribution="carrier-1"))

    assert links._refused and "carrier-1" not in repr(links._refused)


async def test_a_link_is_handed_out_only_on_behalf_of_whom_it_was_issued_to(links, backend, clock) -> None:
    """A link is a credential: the owner's is not given to the daemon or to another caller."""
    from aion.server.files.storage import UploadContext

    task = task_with(stored_part())
    clock.advance(hours=2)

    granted = await links.refresh(task, context=UploadContext(usage_attribution="carrier-1"))
    again = await links.refresh(task, context=UploadContext(usage_attribution="carrier-1"))
    backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
    pushed = await links.refresh(task)
    other = await links.refresh(task, context=UploadContext(usage_attribution="carrier-2"))

    assert again.artifacts[0].parts[0].url == granted.artifacts[0].parts[0].url != STORED_LINK
    assert pushed is task and other is task
    assert backend.asked == [("file-1", "version-1")] * 3


def protected_part(file_id="file-1", *, byte_size=None) -> Part:
    """A part as the Aion backend stores it: the version's address, which works only with authorization."""
    part = Part(url=f"https://files.test/files/{file_id}/versions/version-1/content", filename="report.pdf")
    StoredFile.create(
        file_id, "version-1", retention_expires_at=NOON + timedelta(days=30), byte_size=byte_size
    ).write_to(part)
    return part


async def store(manager: FileUploadManager, carrier) -> None:
    """Store one file through ``manager`` on behalf of ``carrier``; the backend issues its link with it."""
    from aion.server.files.storage import FileUpload, UploadContext

    await manager.store_many([FileUpload(b"%PDF")], context=UploadContext(usage_attribution=carrier))


class UploadingBackend(LinkBackend):
    """Stores files as the Aion backend does: the address in ``uri``, the link issued with it in ``link``."""

    async def store_many(self, uploads, *, context):
        return [
            UploadReceipt(
                uri="https://files.test/files/file-1/versions/version-1/content",
                link="https://files.test/content?grant=upload",
                file_id="file-1",
                version_id="version-1",
                access_expires_at=(self._clock() + timedelta(hours=1)).isoformat(),
            )
            for _ in uploads
        ]


async def test_a_part_with_a_protected_address_is_always_given_a_link(links, backend) -> None:
    part = protected_part()

    assert await links.link_for(part) is not None
    fresh = await links.refresh(task_with(part))
    assert fresh.artifacts[0].parts[0].url.startswith("https://files.test/content?grant=new")
    assert StoredFile.of(fresh.artifacts[0].parts[0]).access_expires_at is not None


async def test_the_link_a_file_was_stored_with_serves_whoever_stored_it(clock) -> None:
    from aion.server.files.storage import UploadContext

    backend = UploadingBackend(clock)
    manager = FileUploadManager(backend)
    links = StoredFileLinks(manager, now=clock)
    await store(manager, "carrier-1")
    task = task_with(protected_part())

    answered = await links.refresh(task, context=UploadContext(usage_attribution="carrier-1"))
    other = await links.refresh(task, context=UploadContext(usage_attribution="carrier-2"))

    assert answered.artifacts[0].parts[0].url == "https://files.test/content?grant=upload"
    assert other.artifacts[0].parts[0].url != "https://files.test/content?grant=upload"
    assert backend.asked == [("file-1", "version-1")]


async def test_a_turn_reads_the_files_it_stored_by_the_link_they_were_stored_with(clock, monkeypatch) -> None:
    """Inside a turn a read asks with the turn's attribution, which is the one its files were stored under."""
    from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution

    backend = UploadingBackend(clock)
    manager = FileUploadManager(backend)
    links = StoredFileLinks(manager, now=clock)
    await store(manager, "carrier-1")
    turn = AionRuntimeContext(callback_attribution=ForwardedAttribution("carrier-1"))
    monkeypatch.setattr(links_module, "get_aion_runtime_context", lambda: turn)

    link = await links.link_for(protected_part())

    assert link == "https://files.test/content?grant=upload"
    assert backend.asked == []


async def test_a_link_keeps_the_parts_own_reference(links, backend) -> None:
    fresh = await links.refresh(task_with(protected_part(byte_size=4096)))

    assert StoredFile.of(fresh.artifacts[0].parts[0]).byte_size == 4096


def test_the_link_scope_is_read_from_the_call_context() -> None:
    from types import SimpleNamespace

    from aion.server.files.links import FILE_LINK_SCOPE_KEY, link_scope_of
    from aion.server.files.storage import UploadContext

    scope = UploadContext(usage_attribution="carrier-1")

    assert link_scope_of(SimpleNamespace(state={FILE_LINK_SCOPE_KEY: scope})) is scope
    assert link_scope_of(SimpleNamespace(state={})) is None
    assert link_scope_of(None) is None


async def test_a_backend_that_issues_no_links_leaves_the_stored_one(clock) -> None:
    class NoLinks(FileStorageBackend):
        async def store_many(self, uploads, *, context):
            raise AssertionError

    links = StoredFileLinks(FileUploadManager(NoLinks()), now=clock)
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert await links.refresh(task) is task


async def test_a_backend_that_raises_leaves_the_stored_link(links, backend, clock) -> None:
    backend.answer = RuntimeError("storage is down")
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert await links.refresh(task) is task


async def test_a_new_link_too_short_lived_to_hand_out_is_not_used(clock) -> None:
    backend = LinkBackend(clock, lifetime=timedelta(minutes=1))
    links = StoredFileLinks(FileUploadManager(backend), now=clock)
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert await links.refresh(task) is task


async def test_an_answer_does_not_wait_for_a_link_that_does_not_come(links, backend, clock, monkeypatch) -> None:
    monkeypatch.setattr(links_module, "RENEWAL_TIMEOUT_SECONDS", 0.05)
    backend.delay = 5
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert await asyncio.wait_for(links.refresh(task), timeout=1) is task
    # The file is left alone for a while rather than waited for on every read.
    assert await asyncio.wait_for(links.refresh(task), timeout=1) is task
    assert len(backend.asked) == 1


async def test_a_file_past_its_retention_is_not_asked_about(links, backend, clock) -> None:
    task = task_with(stored_part(retained_for=timedelta(hours=3)))
    clock.advance(hours=4)

    assert await links.refresh(task) is task
    assert backend.asked == []


async def test_a_part_that_names_no_file_keeps_its_address(links, backend, clock) -> None:
    task = task_with(Part(url="https://example.test/report.pdf", filename="report.pdf"), Part(text="done"))
    clock.advance(hours=2)

    assert await links.refresh(task) is task
    assert backend.asked == []


async def test_only_the_expired_links_of_an_answer_change(links, clock) -> None:
    expired, working = stored_part("file-1"), stored_part("file-2", expires_in=timedelta(hours=5))
    task = task_with(expired, working, Part(text="note"))
    clock.advance(hours=2)

    fresh = await links.refresh(task)

    first, second, note = fresh.artifacts[0].parts
    assert first.url != STORED_LINK and second.url == STORED_LINK and note.text == "note"


@pytest.mark.parametrize("build", [
    lambda part: Message(message_id="m", role=Role.ROLE_USER, parts=[part]),
    lambda part: Artifact(artifact_id="a", parts=[part]),
    lambda part: TaskStatus(
        state=TaskState.TASK_STATE_INPUT_REQUIRED, message=Message(message_id="m", role=Role.ROLE_AGENT, parts=[part])
    ),
    lambda part: TaskArtifactUpdateEvent(
        task_id="task-1", context_id="context-1", artifact=Artifact(artifact_id="a", parts=[part])
    ),
    lambda part: Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=[Message(message_id="m", role=Role.ROLE_USER, parts=[part])],
    ),
], ids=["message", "artifact", "status", "artifact update", "task history"])
async def test_every_shape_that_carries_parts_is_refreshed(links, clock, build) -> None:
    content = build(stored_part())
    clock.advance(hours=2)

    fresh = await links.refresh(content)

    assert fresh is not content
    assert "grant=new-1" in str(fresh) and "grant=stored" not in str(fresh)
    assert "grant=stored" in str(content)


async def test_what_carries_no_parts_passes_through(links, clock) -> None:
    clock.advance(hours=2)

    assert await links.refresh("not an event") == "not an event"
    assert await links.refresh_many([]) == []


async def test_without_a_storage_backend_nothing_is_refreshed(clock) -> None:
    links = StoredFileLinks(None, now=clock)
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert not links.is_active
    assert await links.refresh(task) is task
    assert await links.link_for(task.artifacts[0].parts[0]) is None


async def test_a_reader_gets_no_link_while_the_parts_own_works(links, backend) -> None:
    assert await links.link_for(stored_part(expires_in=timedelta(minutes=30))) is None
    assert backend.asked == []


async def test_a_reader_gets_a_new_link_once_the_parts_own_expired(links, backend, clock) -> None:
    part = stored_part()
    clock.advance(hours=2)

    assert await links.link_for(part) == "https://files.test/content?grant=new-1"
    # The answer that carries the same file reuses the reader's link.
    assert (await links.refresh(task_with(part))).artifacts[0].parts[0].url == "https://files.test/content?grant=new-1"
    assert backend.asked == [("file-1", "version-1")]


async def test_a_closed_manager_issues_no_links(backend, clock) -> None:
    manager = FileUploadManager(backend)
    links = StoredFileLinks(manager, now=clock)
    await manager.aclose()
    task = task_with(stored_part())
    clock.advance(hours=2)

    assert await links.refresh(task) is task
    assert backend.asked == []


class TestRefusedToTheReader:
    """The storage service refusing the grant to the attribution reading a file means it is not available to it.

    Only that refusal (``STORAGE_FORBIDDEN`` for a usage attribution) raises
    ``ConversationFileForbiddenError`` from ``link_for``; every other failure
    leaves the reader with the part's address, and an answer keeps the
    address of a part whose link was refused either way.
    """

    @staticmethod
    def _as(carrier: str):
        from aion.server.files.storage import UploadContext

        return UploadContext(usage_attribution=carrier)

    async def test_a_file_refused_to_the_reader_is_not_available_to_it(self, links, backend) -> None:
        from aion.core.exceptions import ConversationFileForbiddenError

        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
        part = protected_part()
        part.media_type = "application/pdf"

        with pytest.raises(ConversationFileForbiddenError) as refused:
            await links.link_for(part, context=self._as("carrier-1"))
        # The refusal is remembered for the attribution, and answers the next read.
        with pytest.raises(ConversationFileForbiddenError):
            await links.link_for(part, context=self._as("carrier-1"))

        assert str(refused.value) == "File 'report.pdf' (application/pdf) is not available to this sender."
        assert not any(secret in str(refused.value) for secret in ("https://", "grant", "carrier-1"))
        assert backend.asked == [("file-1", "version-1")]

    async def test_a_reader_in_a_turn_is_refused_on_behalf_of_the_turns_attribution(self, links, backend, monkeypatch) -> None:
        from aion.core.exceptions import ConversationFileForbiddenError
        from aion.core.runtime.context import AionRuntimeContext, ForwardedAttribution

        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
        turn = AionRuntimeContext(callback_attribution=ForwardedAttribution("carrier-1"))
        monkeypatch.setattr(links_module, "get_aion_runtime_context", lambda: turn)

        with pytest.raises(ConversationFileForbiddenError):
            await links.link_for(protected_part())

    async def test_a_refusal_to_one_attribution_does_not_make_the_file_unavailable_to_another(self, links, backend) -> None:
        from aion.core.exceptions import ConversationFileForbiddenError

        part = protected_part()
        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
        with pytest.raises(ConversationFileForbiddenError):
            await links.link_for(part, context=self._as("carrier-1"))

        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True)
        assert await links.link_for(part, context=self._as("carrier-2")) is None
        backend.answer = None
        assert await links.link_for(part, context=self._as("carrier-3")) == "https://files.test/content?grant=new-3"
        assert backend.asked == [("file-1", "version-1")] * 3

    @pytest.mark.parametrize("answer", [
        UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True),
        UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
        UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED),
        UploadFailure(FileUploadErrorCode.NO_DAEMON_IDENTITY),
        RuntimeError("storage is down"),
    ], ids=["unavailable", "rejected", "unauthorized", "no daemon identity", "error"])
    async def test_any_other_failure_leaves_the_reader_with_the_address(self, links, backend, answer) -> None:
        backend.answer = answer

        assert await links.link_for(protected_part(), context=self._as("carrier-1")) is None
        # Remembered, it still is no refusal of the file to the reader.
        assert await links.link_for(protected_part(), context=self._as("carrier-1")) is None
        assert backend.asked == [("file-1", "version-1")]

    async def test_a_timeout_leaves_the_reader_with_the_address(self, links, backend, monkeypatch) -> None:
        monkeypatch.setattr(links_module, "RENEWAL_TIMEOUT_SECONDS", 0.05)
        backend.delay = 5

        assert await asyncio.wait_for(links.link_for(protected_part(), context=self._as("carrier-1")), timeout=1) is None
        assert await links.link_for(protected_part(), context=self._as("carrier-1")) is None

    async def test_a_refusal_to_the_daemon_leaves_the_reader_with_the_address(self, links, backend) -> None:
        """Outside a turn, with no attribution, the grant is asked for as the deployment's daemon."""
        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)

        assert await links.link_for(protected_part()) is None

    async def test_an_answer_keeps_the_address_of_a_file_refused_to_its_requester(self, links, backend) -> None:
        from aion.core.exceptions import ConversationFileForbiddenError

        backend.answer = UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)
        task = task_with(protected_part())

        assert await links.refresh(task, context=self._as("carrier-1")) is task
        # The refusal the answer met tells a reader of the same attribution, without asking again.
        with pytest.raises(ConversationFileForbiddenError):
            await links.link_for(task.artifacts[0].parts[0], context=self._as("carrier-1"))
        assert await links.refresh(task, context=self._as("carrier-1")) is task
        assert await links.refresh_many([task], context=self._as("carrier-1")) == [task]
        assert task.artifacts[0].parts[0].url == "https://files.test/files/file-1/versions/version-1/content"
        assert backend.asked == [("file-1", "version-1")]

    async def test_the_owner_is_given_a_link_after_another_was_refused(self, clock) -> None:
        from aion.core.exceptions import ConversationFileForbiddenError

        class OwnerOnly(UploadingBackend):
            async def renew_link(self, file_id, version_id, *, context=None):
                self.asked.append((file_id, version_id))
                return UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)

        backend = OwnerOnly(clock)
        manager = FileUploadManager(backend)
        links = StoredFileLinks(manager, now=clock)
        await store(manager, "carrier-owner")

        with pytest.raises(ConversationFileForbiddenError):
            await links.link_for(protected_part(), context=self._as("carrier-other"))
        assert await links.link_for(protected_part(), context=self._as("carrier-owner")) == (
            "https://files.test/content?grant=upload"
        )
