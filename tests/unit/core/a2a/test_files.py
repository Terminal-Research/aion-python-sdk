"""The reference a stored file part keeps to its file (``aion:file``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from a2a.types import Part

from aion.core.a2a.files import STORED_FILE_KEY, StoredFile, forget_stored_file

NOON = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def stored_part(**metadata) -> Part:
    part = Part(url="https://files.test/content?grant=secret", filename="report.pdf")
    if metadata:
        part.metadata.update(metadata)
    return part


def test_a_reference_survives_the_part_it_is_written_to() -> None:
    reference = StoredFile.create(
        "file-1", "version-2", access_expires_at="2026-11-10T13:00:00Z", retention_expires_at=NOON + timedelta(days=30)
    )
    part = stored_part()
    reference.write_to(part)

    assert StoredFile.of(part) == reference
    assert dict(part.metadata[STORED_FILE_KEY]) == {
        "fileId": "file-1",
        "fileVersionId": "version-2",
        "accessExpiresAt": "2026-11-10T13:00:00Z",
        "retentionExpiresAt": "2026-12-10T12:00:00Z",
    }


def test_writing_a_reference_keeps_the_parts_other_metadata() -> None:
    part = stored_part(custom={"kept": True})
    StoredFile.create("file-1", "version-2").write_to(part)

    assert part.metadata["custom"]["kept"] is True
    assert dict(part.metadata[STORED_FILE_KEY]) == {"fileId": "file-1", "fileVersionId": "version-2"}


def test_a_part_without_both_ids_names_no_file() -> None:
    assert StoredFile.of(stored_part()) is None
    assert StoredFile.of(stored_part(**{STORED_FILE_KEY: {"fileId": "file-1"}})) is None
    assert StoredFile.of(stored_part(**{STORED_FILE_KEY: {"fileId": "", "fileVersionId": "v"}})) is None
    assert StoredFile.of(stored_part(**{STORED_FILE_KEY: "file-1"})) is None


def test_an_inline_file_carries_no_reference() -> None:
    part = Part(raw=b"bytes", filename="a.bin")
    part.metadata.update({STORED_FILE_KEY: {"fileId": "file-1", "fileVersionId": "version-2"}})

    assert StoredFile.of(part) is None


def test_a_timestamp_that_cannot_be_read_is_not_reported() -> None:
    part = stored_part(**{STORED_FILE_KEY: {
        "fileId": "file-1", "fileVersionId": "version-2", "accessExpiresAt": "soon", "retentionExpiresAt": 5,
    }})

    reference = StoredFile.of(part)
    assert reference is not None
    assert reference.access_expires_at is None and reference.retention_expires_at is None


def test_a_link_counts_as_expired_from_the_margin_before_its_expiry() -> None:
    reference = StoredFile.create("f", "v", access_expires_at=NOON + timedelta(minutes=10))

    assert not reference.access_expired(NOON)
    assert not reference.access_expired(NOON, margin=timedelta(minutes=9))
    assert reference.access_expired(NOON, margin=timedelta(minutes=10))
    assert reference.access_expired(NOON + timedelta(minutes=10))


def test_a_link_with_no_known_expiry_counts_as_expired() -> None:
    assert StoredFile.create("f", "v").access_expired(NOON)


def test_a_file_is_removed_only_past_a_known_retention_deadline() -> None:
    assert not StoredFile.create("f", "v").retention_expired(NOON)
    assert not StoredFile.create("f", "v", retention_expires_at=NOON + timedelta(seconds=1)).retention_expired(NOON)
    assert StoredFile.create("f", "v", retention_expires_at=NOON).retention_expired(NOON)


def test_a_new_link_keeps_the_file_and_its_retention() -> None:
    reference = StoredFile.create("f", "v", access_expires_at=NOON, retention_expires_at=NOON + timedelta(days=30))

    renewed = reference.with_access("2026-11-10T14:00:00Z")

    assert (renewed.file_id, renewed.version_id) == ("f", "v")
    assert renewed.access_expires_at == NOON + timedelta(hours=2)
    assert renewed.retention_expires_at == reference.retention_expires_at


def test_forgetting_removes_only_the_reference() -> None:
    part = stored_part(custom="kept", **{STORED_FILE_KEY: {"fileId": "f", "fileVersionId": "v"}})

    assert forget_stored_file(part) is True
    assert STORED_FILE_KEY not in part.metadata and part.metadata["custom"] == "kept"
    assert forget_stored_file(part) is False


def test_forgetting_the_only_key_leaves_the_part_without_metadata() -> None:
    part = stored_part(**{STORED_FILE_KEY: {"fileId": "f", "fileVersionId": "v"}})

    forget_stored_file(part)

    assert not part.HasField("metadata")
