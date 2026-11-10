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
