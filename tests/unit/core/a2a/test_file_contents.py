"""The contents of the files a turn reads: kept for the turn, under the file's identity, and gone with it."""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import Part

from aion.core.a2a.file_contents import FileContents, content_key, file_contents, keeping_file_contents
from aion.core.a2a.files import StoredFile
from aion.core.settings import file_settings


def _stored_part(url: str, file_id: str = "f-1", version_id: str = "v-1") -> Part:
    part = Part(url=url, filename="report.pdf")
    StoredFile.create(file_id, version_id).write_to(part)
    return part


def test_a_stored_file_is_the_version_it_names_whatever_its_link() -> None:
    first = _stored_part("https://files.test/f-1?grant=a")
    renewed = _stored_part("https://files.test/f-1?grant=b")

    assert content_key(first) == content_key(renewed) == "file:f-1:v-1"


def test_an_address_the_server_did_not_store_is_its_own_identity() -> None:
    assert content_key(Part(url="https://example.test/a.png")) == "url:https://example.test/a.png"
    assert content_key(Part(raw=b"bytes")) is None
    assert content_key(Part(text="hello")) is None


def test_content_is_found_by_its_key_and_by_the_address_it_was_kept_with() -> None:
    contents = FileContents()

    assert contents.put("file:f-1:v-1", b"content", url="https://files.test/f-1?grant=a")

    assert contents.get("file:f-1:v-1") == b"content"
    assert contents.get_by_url("https://files.test/f-1?grant=a") == b"content"
    assert contents.get_by_url("https://files.test/other") is None


def test_a_file_no_model_request_may_carry_is_not_kept(monkeypatch) -> None:
    monkeypatch.setattr(file_settings, "model_file_max_bytes", 4)
    contents = FileContents()

    assert not contents.put("big", b"12345")
    assert contents.put("small", b"1234")
    assert contents.get("big") is None


def test_each_turn_has_its_own_and_none_outlives_its_turn() -> None:
    first, second = FileContents(), FileContents()

    with keeping_file_contents(first):
        file_contents().put("a", b"1")
    with keeping_file_contents(second):
        assert file_contents().get("a") is None
    assert first.get("a") == b"1"


def test_outside_a_turn_nothing_is_kept() -> None:
    """The test's own turn is left for a moment: there is none outside it."""
    from aion.core.a2a import file_contents as module

    token = module._current.set(None)
    try:
        assert not file_contents().put("a", b"1")
        assert file_contents().get("a") is None
        assert file_contents().mark_too_large("a")
        assert not file_contents().is_too_large("a")
    finally:
        module._current.reset(token)


async def test_outside_a_turn_every_read_downloads() -> None:
    from aion.core.a2a import file_contents as module

    downloads = 0

    async def download() -> bytes:
        nonlocal downloads
        downloads += 1
        return b"content"

    token = module._current.set(None)
    try:
        await file_contents().load("a", download)
        await file_contents().load("a", download)
    finally:
        module._current.reset(token)

    assert downloads == 2


async def test_readers_of_one_file_share_one_download() -> None:
    contents = FileContents()
    downloads = 0
    release = asyncio.Event()

    async def download() -> bytes:
        nonlocal downloads
        downloads += 1
        await release.wait()
        return b"content"

    readers = [asyncio.create_task(contents.load("f", download)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*readers) == [b"content"] * 3
    assert downloads == 1


async def test_a_load_finds_content_kept_under_another_key_by_its_address() -> None:
    """A file stored this turn is kept under its version; a reader with its address alone finds it."""
    contents = FileContents()
    contents.put("file:f-1:v-1", b"content", url="https://files.test/f-1?grant=a")

    async def download() -> bytes:
        raise AssertionError("the file is downloaded again")

    assert await contents.load("url:https://files.test/f-1?grant=a", download, url="https://files.test/f-1?grant=a") == b"content"


async def test_a_failed_download_is_not_kept() -> None:
    contents = FileContents()

    async def fail() -> bytes:
        raise ConnectionError("unreachable")

    async def succeed() -> bytes:
        return b"content"

    with pytest.raises(ConnectionError):
        await contents.load("f", fail)

    assert await contents.load("f", succeed) == b"content"


def test_a_file_too_large_is_news_to_the_turn_once() -> None:
    contents = FileContents()

    assert contents.mark_too_large("f")
    assert not contents.mark_too_large("f")
    assert contents.is_too_large("f") and not contents.is_too_large("g")


class TestStoredFileByContent:
    """The turn finds the stored file a copy of its bytes came from, when exactly one stored file has them."""

    ADDRESS = "https://files.test/files/f-1/versions/v-1/content"
    REPORT = StoredFile.create("f-1", "v-1", retention_expires_at="2026-12-18T00:00:00Z", byte_size=7)

    def test_the_one_stored_file_with_the_bytes_is_found(self) -> None:
        contents = FileContents()
        contents.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT)

        assert contents.stored_file_with(b"content") == (self.ADDRESS, self.REPORT)
        assert contents.stored_file_with(b"other") is None

    def test_content_kept_without_its_stored_file_leads_nowhere(self) -> None:
        contents = FileContents()
        contents.put("url:https://example.test/a.pdf", b"content", url="https://example.test/a.pdf")

        assert contents.stored_file_with(b"content") is None

    def test_bytes_of_two_stored_files_lead_to_neither(self) -> None:
        contents = FileContents()
        contents.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT)
        contents.put("file:f-2:v-1", b"content", url="https://files.test/f-2", stored=StoredFile.create("f-2", "v-1"))

        assert contents.stored_file_with(b"content") is None

    def test_the_same_file_read_again_is_still_one(self) -> None:
        contents = FileContents()
        contents.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT)
        contents.put("file:f-1:v-1", b"content", url="https://files.test/f-1?grant=g", stored=self.REPORT)

        assert contents.stored_file_with(b"content") == (self.ADDRESS, self.REPORT)

    async def test_a_load_keeps_the_stored_file_it_read(self) -> None:
        contents = FileContents()

        async def download() -> bytes:
            return b"content"

        await contents.load("file:f-1:v-1", download, url=self.ADDRESS, stored=self.REPORT)

        assert contents.stored_file_with(b"content") == (self.ADDRESS, self.REPORT)

    def test_a_media_type_asked_for_must_be_the_one_kept(self) -> None:
        """A stored version is its bytes and its type: the same bytes with another type are another file."""
        contents = FileContents()
        contents.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT, media_type="application/pdf")

        assert contents.stored_file_with(b"content", media_type="application/pdf") == (self.ADDRESS, self.REPORT)
        assert contents.stored_file_with(b"content", media_type="Application/PDF; q=1") == (self.ADDRESS, self.REPORT)
        assert contents.stored_file_with(b"content", media_type="text/plain") is None
        assert contents.stored_file_with(b"content") == (self.ADDRESS, self.REPORT)

    def test_a_media_type_asked_for_is_not_matched_by_one_unknown(self) -> None:
        contents = FileContents()
        contents.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT)

        assert contents.stored_file_with(b"content", media_type="application/pdf") is None

    def test_outside_a_turn_nothing_is_found(self) -> None:
        outside = FileContents(keeps=False)
        outside.put("file:f-1:v-1", b"content", url=self.ADDRESS, stored=self.REPORT)

        assert outside.stored_file_with(b"content") is None
