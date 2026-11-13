"""A turn stores each file once, whether the event pipeline or a framework's state asks first.

The pipeline stores the files an agent sends out; a framework adapter stores
the files its framework keeps in its state, so the state holds a link and not
the bytes. Both ask the turn's files (``aion.server.files.turn``), and the
second ask for the same file gets the link of the first.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from a2a.types import Artifact, Message, Part, Role, TaskArtifactUpdateEvent

from aion.core.a2a.file_contents import content_key, file_contents
from aion.core.a2a.files import StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.server.files.a2a import TurnUploads
from aion.server.files.storage import FileRetentionDefault, FileUpload, FileUploadManager, UploadFailure
from aion.server.files.turn import StateFileNotStoredError
from tests.unit.support.files import NumberingBackend, storing_turn, upload_context

PDF = b"%PDF-1.7 report"
TRANSFORMER_LOGGER = "aion.server.files.a2a.part_transformer.transformer"


def _report(**overrides) -> Part:
    fields = {"raw": PDF, "media_type": "application/pdf", "filename": "report.pdf", **overrides}
    return Part(**fields)


def _artifact_event(part: Part) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id="task-1", context_id="context-1", artifact=Artifact(artifact_id="a-1", parts=[part])
    )


class TestTurnUploads:
    async def test_the_same_file_asked_twice_is_stored_once(self) -> None:
        backend = NumberingBackend()
        manager, turn = FileUploadManager(backend), TurnUploads()
        upload = FileUpload(data=PDF, media_type="application/pdf", filename="report.pdf")

        [(first, stored_first)] = await turn.store_many(manager, [upload], context=upload_context())
        [(second, stored_second)] = await turn.store_many(manager, [upload], context=upload_context())

        assert len(backend.stored) == 1
        assert second is first
        assert (stored_first, stored_second) == (True, False)

    async def test_two_asks_at_once_wait_for_one_upload(self) -> None:
        backend = NumberingBackend(delay=0.05)
        manager, turn = FileUploadManager(backend), TurnUploads()
        upload = FileUpload(data=PDF, media_type="application/pdf")

        first, second = await asyncio.gather(
            turn.store_many(manager, [upload], context=upload_context()),
            turn.store_many(manager, [upload], context=upload_context()),
        )

        assert len(backend.stored) == 1
        assert first[0][0] is second[0][0]

    async def test_a_file_twice_in_one_batch_is_stored_once(self) -> None:
        backend = NumberingBackend()
        upload = FileUpload(data=PDF, media_type="application/pdf")

        outcomes = await TurnUploads().store_many(
            FileUploadManager(backend), [upload, upload], context=upload_context()
        )

        assert len(backend.stored) == 1
        assert [stored_here for _, stored_here in outcomes] == [True, False]

    async def test_the_name_does_not_make_another_file(self) -> None:
        backend = NumberingBackend()
        manager, turn = FileUploadManager(backend), TurnUploads()

        await turn.store_many(manager, [FileUpload(data=PDF, media_type="application/pdf", filename="a.pdf")], context=upload_context())
        await turn.store_many(manager, [FileUpload(data=PDF, media_type="application/pdf")], context=upload_context())

        assert [upload.filename for upload in backend.stored] == ["a.pdf"]

    @pytest.mark.parametrize(
        "other",
        [
            FileUpload(data=b"another report", media_type="application/pdf"),
            FileUpload(data=PDF, media_type="text/plain"),
            FileUpload(data=PDF, media_type="application/pdf", retention_expires_at=None),
        ],
        ids=["content", "media-type", "retention"],
    )
    async def test_other_content_type_or_retention_is_another_file(self, other: FileUpload) -> None:
        backend = NumberingBackend()
        manager, turn = FileUploadManager(backend), TurnUploads()
        first = FileUpload(data=PDF, media_type="application/pdf", retention_expires_at=FileRetentionDefault.PROVIDER)

        await turn.store_many(manager, [first], context=upload_context())
        await turn.store_many(manager, [other], context=upload_context())

        assert len(backend.stored) == 2

    async def test_a_file_that_was_not_stored_is_tried_again(self) -> None:
        backend = NumberingBackend(refuse=True)
        manager, turn = FileUploadManager(backend), TurnUploads()
        upload = FileUpload(data=PDF, media_type="application/pdf")

        [(refused, _)] = await turn.store_many(manager, [upload], context=upload_context())
        backend.refuse = False
        [(stored, stored_here)] = await turn.store_many(manager, [upload], context=upload_context())

        assert isinstance(refused, UploadFailure)
        assert stored.file_id == "file-1" and stored_here is True

    async def test_an_ask_that_stops_waiting_does_not_stop_the_upload(self) -> None:
        backend = NumberingBackend(delay=0.05)
        manager, turn = FileUploadManager(backend), TurnUploads()
        upload = FileUpload(data=PDF, media_type="application/pdf")

        impatient = asyncio.ensure_future(turn.store_many(manager, [upload], context=upload_context()))
        await asyncio.sleep(0)
        patient = asyncio.ensure_future(turn.store_many(manager, [upload], context=upload_context()))
        await asyncio.sleep(0)
        impatient.cancel()

        [(outcome, stored_here)] = await patient
        assert outcome.file_id == "file-1" and stored_here is False
        assert len(backend.stored) == 1


class TestTurnFiles:
    async def test_state_and_pipeline_hold_the_same_link_to_one_stored_file(self) -> None:
        backend = NumberingBackend()
        with storing_turn(backend) as files:
            [kept] = await files.keep_parts([_report()])
            sent = await files._transformer.transform(_artifact_event(_report()), turn=files.uploads)

        [delivered] = sent.event.artifact.parts
        assert len(backend.stored) == 1
        assert kept.url == delivered.url == "https://files.test/1?grant=g"
        assert StoredFile.of(kept) == StoredFile.of(delivered)
        assert StoredFile.of(kept).file_id == "file-1"

    async def test_a_stored_file_is_logged_once(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger=TRANSFORMER_LOGGER), storing_turn(NumberingBackend()) as files:
            await files.keep_parts([_report()])
            await files.keep_parts([_report()])

        stored_lines = [record for record in caplog.records if record.getMessage().startswith("Stored ")]
        assert len(stored_lines) == 1

    async def test_the_stored_content_is_kept_for_a_later_read(self) -> None:
        file_contents().clear()
        with storing_turn(NumberingBackend()) as files:
            [kept] = await files.keep_parts([_report()])

        assert file_contents().get(content_key(kept)) == PDF
        assert file_contents().get_by_url(kept.url) == PDF

    async def test_parts_without_inline_content_are_left_as_they_are(self) -> None:
        backend = NumberingBackend()
        parts = [Part(text="hello"), Part(url="https://elsewhere.test/a.png", media_type="image/png")]
        with storing_turn(backend) as files:
            kept = await files.keep_parts(parts)

        assert kept == parts and backend.stored == []

    async def test_a_file_that_cannot_be_stored_fails_with_a_reason_for_the_client(self) -> None:
        with storing_turn(NumberingBackend(refuse=True)) as files:
            with pytest.raises(StateFileNotStoredError) as raised:
                await files.keep_parts([_report()])

        assert raised.value.public_reason
        assert "\n" not in raised.value.public_reason

    async def test_an_outbox_keeps_its_files_as_links(self) -> None:
        outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="done"), _report()]))
        with storing_turn(NumberingBackend()) as files:
            kept = await files.keep_outbox(outbox)

        text, report = kept.message.parts
        assert text.text == "done"
        assert report.url == "https://files.test/1?grant=g" and not report.raw
        assert outbox.message.parts[1].raw == PDF

    async def test_an_outbox_without_files_is_returned_as_it_is(self) -> None:
        outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="done")]))
        with storing_turn(NumberingBackend()) as files:
            assert await files.keep_outbox(outbox) is outbox
