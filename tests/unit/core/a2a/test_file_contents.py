"""The in-memory contents of stored files: what is kept, for how long, and under which identity."""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import Part

from aion.core.a2a.file_contents import FileContents, content_key
from aion.core.a2a.files import StoredFile


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _cache(max_bytes: int = 100, ttl_seconds: float = 60.0) -> tuple[FileContents, _Clock]:
    clock = _Clock()
    return FileContents(max_bytes=max_bytes, ttl_seconds=ttl_seconds, clock=clock), clock


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
    cache, _ = _cache()

    assert cache.put("file:f-1:v-1", b"content", url="https://files.test/f-1?grant=a")

    assert cache.get("file:f-1:v-1") == b"content"
    assert cache.get_by_url("https://files.test/f-1?grant=a") == b"content"
    assert cache.get_by_url("https://files.test/other") is None


def test_an_entry_not_read_for_the_ttl_is_gone_and_a_read_keeps_it() -> None:
    cache, clock = _cache(ttl_seconds=60)
    cache.put("a", b"1")
    cache.put("b", b"2")

    clock.now = 50
    assert cache.get("a") == b"1"
    clock.now = 100

    assert cache.get("a") == b"1"
    assert cache.get("b") is None
    assert cache.size == 1


def test_forgetting_drops_the_content_and_its_addresses() -> None:
    cache, _ = _cache()
    cache.put("a", b"12345", url="https://files.test/a")

    cache.forget("a")

    assert cache.get("a") is None
    assert cache.get_by_url("https://files.test/a") is None
    assert cache.size == 0


def test_the_entry_read_longest_ago_makes_room() -> None:
    cache, clock = _cache(max_bytes=10)
    cache.put("a", b"12345", url="https://files.test/a")
    cache.put("b", b"12345")
    clock.now = 1
    cache.get("a")

    cache.put("c", b"12345")

    assert cache.get("b") is None
    assert cache.get("a") == b"12345" and cache.get("c") == b"12345"
    assert cache.size == 10


def test_a_file_larger_than_the_budget_is_not_kept() -> None:
    cache, _ = _cache(max_bytes=4)

    assert cache.put("a", b"12345") is False
    assert cache.get("a") is None
    assert _cache(max_bytes=0)[0].put("a", b"1") is False


def test_a_dropped_entry_is_not_found_by_its_address_either() -> None:
    cache, _ = _cache(max_bytes=5)
    cache.put("a", b"12345", url="https://files.test/a")

    cache.put("b", b"12345")

    assert cache.get_by_url("https://files.test/a") is None


async def test_readers_of_one_file_share_one_download() -> None:
    cache, _ = _cache()
    calls = 0

    async def download() -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return b"content"

    results = await asyncio.gather(*(cache.load("a", download) for _ in range(3)))

    assert results == [b"content"] * 3
    assert calls == 1
    assert await cache.load("a", download) == b"content"
    assert calls == 1


async def test_a_load_finds_content_kept_under_another_key_by_its_address() -> None:
    """A stored file is kept under its version; a reader that knows only its address does not download it again."""
    cache, _ = _cache()
    cache.put("file:f-1:v-1", b"content", url="https://files.test/f-1?grant=a")

    async def download() -> bytes:
        raise AssertionError("the file is in memory")

    assert await cache.load("url:https://files.test/f-1?grant=a", download, url="https://files.test/f-1?grant=a") == b"content"
    assert cache.size == len(b"content")


async def test_a_failed_download_is_not_kept() -> None:
    cache, _ = _cache()

    async def fail() -> bytes:
        raise RuntimeError("storage is down")

    async def succeed() -> bytes:
        return b"content"

    with pytest.raises(RuntimeError):
        await cache.load("a", fail)
    assert await cache.load("a", succeed) == b"content"
