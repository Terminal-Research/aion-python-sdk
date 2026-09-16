"""Tests for the upload-first file storage contract.

Covers the outcome contract, the verified projection that names the owning
organization, the outbound rule that drops content which could not be stored,
and the guard that keeps inline bytes out of the task record.

Backend-internal behaviour (retry classification, idempotent ``operation_id``,
authentication diagnostics) lives in ``test_aion_backend.py``; the stub used
here succeeds unconditionally.
"""

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
    TaskStatusUpdateEvent,
)
from aion.core.a2a.extensions import (
    DistributionExtensionV1,
    ServiceIdentity,
)
from aion.core.constants import (
    CARDS_MEDIA_TYPE,
    DISTRIBUTION_EXTENSION_URI_V1,
    USAGE_ATTRIBUTION_EXTENSION_URI_V1,
)
from aion.core.runtime.context import AionRuntimeExtensions
from aion.server.files.a2a import strip_inline_file_content
from aion.server.files.a2a.part_transformer import A2AFileTransformer
from aion.server.files.storage import (
    FileUpload,
    FileUploadErrorCode,
    FileUploadManager,
    StubFileStorageBackend,
    UploadContext,
    UploadFailure,
    UploadReceipt,
    resolve_upload_context,
)

from tests.unit.support.files import (
    ORG,
    OutcomeBackend,
    RecordingBackend,
    distribution_payload,
    principal,
    upload_context,
)


# --------------------------------------------------------------------------
# Fixtures and builders
# --------------------------------------------------------------------------

def service_identity() -> ServiceIdentity:
    return ServiceIdentity(
        kind="service",
        id="sid-1",
        identity_network="Telegram",
        identity_kind="Bot",
        organization_id="org-other",
    )



def extensions(payload: DistributionExtensionV1 | None, carrier: str | None = None):
    verified = {}
    if payload is not None:
        verified[DISTRIBUTION_EXTENSION_URI_V1] = payload
    if carrier is not None:
        verified[USAGE_ATTRIBUTION_EXTENSION_URI_V1] = carrier
    return AionRuntimeExtensions(verified)



def raw_part(data: bytes = b"bytes", name: str = "a.png", media: str = "image/png") -> Part:
    return Part(raw=data, media_type=media, filename=name)



# --------------------------------------------------------------------------
# Projection: which organization owns the file
# --------------------------------------------------------------------------

class TestUploadContextProjection:
    def test_single_principal_identity_gives_its_organization(self):
        """Exactly one principal identity: that identity's organization owns the file."""
        resolved = resolve_upload_context(extensions(distribution_payload(principal())))
        assert isinstance(resolved, UploadContext)
        assert resolved.organization_id == ORG
        assert resolved.distribution_id == "dist-1"

    def test_no_principal_identity_is_a_context_failure(self):
        """A distribution with only a service identity names no owning organization."""
        resolved = resolve_upload_context(
            extensions(distribution_payload(service_identity()))
        )
        assert isinstance(resolved, UploadFailure)
        assert resolved.error_code is FileUploadErrorCode.NO_ORGANIZATION

    def test_several_principal_identities_are_ambiguous(self):
        """Two principals: refuse rather than silently pick who pays."""
        resolved = resolve_upload_context(
            extensions(
                distribution_payload(
                    principal(organization_id="org-a", identity_id="p-a"),
                    principal(organization_id="org-b", identity_id="p-b"),
                )
            )
        )
        assert isinstance(resolved, UploadFailure)
        assert resolved.error_code is FileUploadErrorCode.AMBIGUOUS_ORGANIZATION

    def test_missing_distribution_is_its_own_code(self):
        """No distribution at all is distinguishable from one without a principal."""
        resolved = resolve_upload_context(extensions(None))
        assert isinstance(resolved, UploadFailure)
        assert resolved.error_code is FileUploadErrorCode.NO_DISTRIBUTION

    def test_usage_carrier_is_projected(self):
        """The opaque signed carrier travels with the projection, uninspected."""
        resolved = resolve_upload_context(
            extensions(distribution_payload(principal()), carrier="opaque-token")
        )
        assert resolved.usage_attribution == "opaque-token"

    def test_selector_matches_the_runtime_context(self):
        """The projected selector is the one the runtime context would produce.

        Inbound preprocessing cannot read AionRuntimeContext, so it derives the
        selector from the same environment. If the two ever disagreed, the same
        agent would act as different principals depending on direction.
        """
        from aion.core.runtime.context import AionRuntimeContext

        payload = distribution_payload(principal(), daemon_identity_id="daemon-7")
        resolved = resolve_upload_context(extensions(payload))

        expected = AionRuntimeContext(
            distribution_extension_payload=payload
        ).get_principal_selector()
        assert expected == "aion://agent/identity/daemon-7"
        assert resolved.principal_selector.to_header_value() == expected

    def test_selector_falls_back_to_the_environment(self):
        """Without a daemon identity the environment itself is the principal."""
        resolved = resolve_upload_context(extensions(distribution_payload(principal())))
        assert resolved.principal_selector.to_header_value() == (
            "aion://agent/environment/env-1"
        )


# --------------------------------------------------------------------------
# Manager and backend contract
# --------------------------------------------------------------------------

class TestFileUploadManager:
    async def test_store_returns_a_receipt(self):
        manager = FileUploadManager(StubFileStorageBackend())
        outcome = await manager.store(
            FileUpload(b"x", "text/plain", "n.txt"), context=upload_context()
        )
        assert isinstance(outcome, UploadReceipt)
        assert outcome.uri

    async def test_store_many_is_positional(self):
        """Outcomes line up with the uploads that produced them."""
        backend = OutcomeBackend(
            [
                UploadReceipt(uri="u-0"),
                UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
                UploadReceipt(uri="u-2"),
            ]
        )
        manager = FileUploadManager(backend)
        outcomes = await manager.store_many(
            [FileUpload(b"a"), FileUpload(b"b"), FileUpload(b"c")],
            context=upload_context(),
        )
        assert [getattr(o, "uri", None) for o in outcomes] == ["u-0", None, "u-2"]

    async def test_empty_batch_does_not_reach_the_backend(self):
        backend = RecordingBackend()
        manager = FileUploadManager(backend)
        assert await manager.store_many([], context=upload_context()) == []
        assert backend.batches == []

    async def test_store_after_close_is_refused_per_file(self):
        """Shutdown refuses new work without raising into a response in flight."""
        manager = FileUploadManager(StubFileStorageBackend())
        await manager.aclose()

        outcomes = await manager.store_many(
            [FileUpload(b"a"), FileUpload(b"b")], context=upload_context()
        )
        assert all(isinstance(o, UploadFailure) for o in outcomes)
        assert {o.error_code for o in outcomes} == {FileUploadErrorCode.STORAGE_CLOSED}

    async def test_aclose_is_idempotent(self):
        closed = []

        class CountingBackend(StubFileStorageBackend):
            async def aclose(self):
                closed.append(True)

        manager = FileUploadManager(CountingBackend())
        await manager.aclose()
        await manager.aclose()
        assert manager.is_closed
        assert len(closed) == 1

    async def test_from_settings_returns_none_without_a_backend(self, monkeypatch):
        from aion.server.settings import app_settings

        monkeypatch.setattr(app_settings, "file_storage_backend", None)
        assert FileUploadManager.from_settings() is None

    async def test_from_settings_builds_the_stub(self, monkeypatch):
        from aion.server.settings import app_settings

        monkeypatch.setattr(app_settings, "file_storage_backend", "stub")
        assert isinstance(FileUploadManager.from_settings(), FileUploadManager)

    async def test_from_settings_rejects_an_unknown_backend(self, monkeypatch):
        from aion.server.settings import app_settings

        monkeypatch.setattr(app_settings, "file_storage_backend", "nope")
        with pytest.raises(ValueError, match="Unknown storage backend"):
            FileUploadManager.from_settings()


class TestLeafName:
    def _upload(self, filename, media_type="application/octet-stream"):
        return FileUpload(data=b"x", media_type=media_type, filename=filename)

    def test_directory_components_are_dropped(self):
        assert self._upload("../../etc/passwd").leaf_name("id") == "passwd.bin"

    def test_hostile_characters_are_replaced(self):
        assert self._upload('a b;"x".png').leaf_name("id") == "a-b-x.png"

    def test_extension_comes_from_the_media_type_when_absent(self):
        assert self._upload("report", "application/pdf").leaf_name("id") == "report.pdf"

    def test_empty_name_falls_back_to_the_upload_id(self):
        assert self._upload(None, "text/plain").leaf_name("abc").startswith("abc.")


# --------------------------------------------------------------------------
# Transformer
# --------------------------------------------------------------------------

def status_event(*parts, context_id="ctx-1") -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id="t-1",
        context_id=context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING,
            message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=list(parts)),
        ),
    )


def artifact_event(*parts, context_id="ctx-1") -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id="t-1",
        context_id=context_id,
        artifact=Artifact(artifact_id="a-1", name="art", parts=list(parts)),
    )


class TestA2AFileTransformer:
    def _transformer(self, backend=None):
        return A2AFileTransformer(
            upload_manager=FileUploadManager(backend or StubFileStorageBackend())
        )

    def test_inactive_without_a_manager(self):
        assert A2AFileTransformer(upload_manager=None).is_active is False

    async def test_no_manager_passes_events_through(self):
        transformer = A2AFileTransformer(upload_manager=None)
        event = status_event(raw_part())
        assert await transformer.transform_event(event) is event

    async def test_status_event_raw_becomes_url(self):
        event = await self._transformer().transform_event(
            status_event(raw_part()), upload_context=upload_context()
        )
        part = event.status.message.parts[0]
        assert part.url and not part.raw

    async def test_artifact_event_raw_becomes_url(self):
        event = await self._transformer().transform_event(
            artifact_event(raw_part()), upload_context=upload_context()
        )
        assert event.artifact.parts[0].url

    async def test_event_without_inline_parts_is_returned_unchanged(self):
        event = status_event(Part(text="hello"))
        assert await self._transformer().transform_event(
            event, upload_context=upload_context()
        ) is event

    async def test_card_parts_are_never_stored(self):
        """Cards are UI documents, not files - the skip rules keep them inline."""
        backend = RecordingBackend()
        event = status_event(Part(raw=b"<Card/>", media_type=CARDS_MEDIA_TYPE))
        result = await self._transformer(backend).transform_event(
            event, upload_context=upload_context()
        )
        assert result is event
        assert backend.batches == []

    async def test_one_batch_per_event(self):
        """Every inline part of one event is stored in a single call."""
        backend = RecordingBackend()
        await self._transformer(backend).transform_event(
            status_event(raw_part(name="a.png"), Part(text="t"), raw_part(name="b.png")),
            upload_context=upload_context(),
        )
        assert len(backend.batches) == 1
        assert [u.filename for u in backend.batches[0]] == ["a.png", "b.png"]

    async def test_part_order_survives_concurrent_storage(self):
        """Uploads run concurrently; parts are restored by index, not by arrival."""
        backend = OutcomeBackend(
            [UploadReceipt(uri=f"u-{i}") for i in range(4)], delay=0.01
        )
        event = await self._transformer(backend).transform_event(
            status_event(*(raw_part(name=f"{i}.png") for i in range(4))),
            upload_context=upload_context(),
        )
        assert [p.url for p in event.status.message.parts] == [f"u-{i}" for i in range(4)]
        assert backend.concurrent_peak > 1

    async def test_a_failed_part_does_not_cancel_its_neighbours(self):
        """Policy is 'let the rest arrive', so one failure keeps the others."""
        backend = OutcomeBackend(
            [
                UploadReceipt(uri="u-0"),
                UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
                UploadReceipt(uri="u-2"),
            ]
        )
        event = await self._transformer(backend).transform_event(
            status_event(*(raw_part(name=f"{i}.png") for i in range(3))),
            upload_context=upload_context(),
        )
        parts = event.status.message.parts
        assert [p.url for p in parts] == ["u-0", "u-2"]

    async def test_failed_part_is_dropped_and_logged(self, caplog):
        """Never inline bytes - that is what the transformer exists to prevent."""
        backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True)])
        with caplog.at_level("WARNING"):
            event = await self._transformer(backend).transform_event(
                status_event(Part(text="answer"), raw_part(name="a.png")),
                upload_context=upload_context(),
            )
        parts = event.status.message.parts
        assert [p.text for p in parts] == ["answer"]
        assert "a.png" in caplog.text
        assert "STORAGE_UNAVAILABLE" in caplog.text

    async def test_dropping_never_publishes_the_cause(self, caplog):
        """The exception is logged, not handed to whoever reads the event."""
        cause = RuntimeError("https://internal.host/secret timed out")
        backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_REJECTED, cause=cause)])
        with caplog.at_level("WARNING"):
            event = await self._transformer(backend).transform_event(
                status_event(raw_part()), upload_context=upload_context()
            )
        assert "internal.host" not in event.SerializeToString().decode("latin-1")
        assert "internal.host" in caplog.text

    async def test_context_failure_is_reported_once_for_the_batch(self):
        """Five attachments under one broken request produce one diagnostic."""
        backend = RecordingBackend()
        transformer = self._transformer(backend)
        message = Message(
            message_id="m-1",
            role=Role.ROLE_USER,
            parts=[raw_part(name=f"{i}.png") for i in range(5)],
        )
        failure = UploadFailure(FileUploadErrorCode.NO_DISTRIBUTION)

        result = await transformer.transform_message(message, upload_context=failure)

        assert len(result.report.failures) == 1
        assert backend.batches == []
        assert list(result.message.parts) == []

    async def test_report_receipts_match_the_stored_parts(self):
        transform = await self._transformer().transform_message(
            Message(
                message_id="m-1",
                role=Role.ROLE_USER,
                parts=[raw_part(), Part(text="t"), raw_part(name="b.png")],
            ),
            upload_context=upload_context(),
        )
        assert len(transform.report.receipts) == 2
        assert transform.report.ok
        urls = [p.url for p in transform.message.parts if p.url]
        assert urls == [r.uri for r in transform.report.receipts]

    async def test_source_message_is_not_mutated(self):
        message = Message(message_id="m-1", role=Role.ROLE_USER, parts=[raw_part()])
        transform = await self._transformer().transform_message(
            message, upload_context=upload_context()
        )
        assert transform.message is not message
        assert message.parts[0].raw


# --------------------------------------------------------------------------
# Inline-content guard
# --------------------------------------------------------------------------

def task_with(status_parts=(), history_parts=(), artifact_parts=()) -> Task:
    task = Task(id="11111111-1111-1111-1111-111111111111", context_id="ctx-1")
    task.status.CopyFrom(
        TaskStatus(
            state=TaskState.TASK_STATE_WORKING,
            message=Message(
                message_id="m-status", role=Role.ROLE_AGENT, parts=list(status_parts)
            ),
        )
    )
    if history_parts:
        task.history.append(
            Message(message_id="m-hist", role=Role.ROLE_USER, parts=list(history_parts))
        )
    if artifact_parts:
        task.artifacts.append(
            Artifact(artifact_id="a-1", name="art", parts=list(artifact_parts))
        )
    return task


class TestInlineContentGuard:
    @pytest.mark.parametrize(
        "field", ["status_parts", "history_parts", "artifact_parts"]
    )
    def test_raw_is_stripped_everywhere_it_can_hide(self, field):
        """status, history and artifacts are written separately - all three are guarded."""
        task = task_with(**{field: [raw_part()]})
        guarded = strip_inline_file_content(task)

        remaining = [
            part
            for message in list(guarded.history) + [guarded.status.message]
            for part in message.parts
        ] + [part for artifact in guarded.artifacts for part in artifact.parts]
        assert remaining == []

    def test_guard_does_not_mutate_the_original_task(self):
        """The same object is already in the task manager and on its way out."""
        task = task_with(status_parts=[raw_part()])
        guarded = strip_inline_file_content(task)

        assert guarded is not task
        assert task.status.message.parts[0].raw == b"bytes"

    def test_neighbouring_parts_survive(self, caplog):
        """Only the inline file goes; the strike is logged as the bug it is."""
        with caplog.at_level("ERROR"):
            guarded = strip_inline_file_content(
                task_with(status_parts=[Part(text="keep"), raw_part()])
            )
        assert [p.text for p in guarded.status.message.parts] == ["keep"]
        assert guarded.id in caplog.text

    def test_clean_task_is_returned_as_is(self):
        task = task_with(status_parts=[Part(text="hello")])
        assert strip_inline_file_content(task) is task

    def test_card_parts_survive_the_guard(self):
        """The guard follows the transformer's skip rules or it would eat cards."""
        card = Part(raw=b"<Card/>", media_type=CARDS_MEDIA_TYPE)
        task = task_with(status_parts=[card])
        assert strip_inline_file_content(task) is task



class TestStoreGuard:
    """The guard is a property of the store, set where the backend is installed."""

    @staticmethod
    def _store(**kwargs):
        from aion.server.tasks.stores import InMemoryTaskStore

        return InMemoryTaskStore(owner_resolver=lambda _context: "owner", **kwargs)

    async def _saved(self, store, task):
        await store.save(task)
        return await store.get(task.id)

    async def test_a_guarded_store_strips_before_writing(self):
        task = task_with(status_parts=[Part(text="keep"), raw_part()])
        saved = await self._saved(self._store(guard_inline_files=True), task)

        assert [p.text for p in saved.status.message.parts] == ["keep"]
        assert task.status.message.parts[1].raw == b"bytes"

    async def test_an_unguarded_store_keeps_the_bytes(self):
        """Passthrough is a supported mode: with no backend, bytes belong in the row."""
        task = task_with(status_parts=[raw_part()])
        saved = await self._saved(self._store(), task)

        assert saved.status.message.parts[0].raw == b"bytes"

    def test_store_manager_builds_the_store_it_was_asked_for(self):
        """Whoever installs the backend says so once; the store never reads settings."""
        from aion.server.tasks import StoreManager

        guarded, plain = StoreManager(), StoreManager()
        guarded.initialize("agent", guard_inline_files=True)
        plain.initialize("agent")

        assert guarded.get_store().guards_inline_files is True
        assert plain.get_store().guards_inline_files is False


# --------------------------------------------------------------------------
# Fault versus retryability
# --------------------------------------------------------------------------

class TestFaultClassification:
    @pytest.mark.parametrize(
        "code",
        [
            FileUploadErrorCode.NO_DISTRIBUTION,
            FileUploadErrorCode.NO_ORGANIZATION,
            FileUploadErrorCode.AMBIGUOUS_ORGANIZATION,
            FileUploadErrorCode.STORAGE_REJECTED,
        ],
    )
    def test_codes_the_sender_must_fix(self, code):
        assert code.client_fault is True

    @pytest.mark.parametrize(
        "code",
        [
            FileUploadErrorCode.STORAGE_UNAVAILABLE,
            FileUploadErrorCode.STORAGE_UNAUTHORIZED,
            FileUploadErrorCode.STORAGE_CLOSED,
        ],
    )
    def test_codes_the_deployment_must_fix(self, code):
        assert code.client_fault is False

    def test_fault_is_independent_of_retryability(self):
        """Refused credentials are neither retryable nor the sender's doing."""
        failure = UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED)
        assert failure.retryable is False
        assert failure.client_fault is False

    def test_public_reason_is_derived_from_the_code(self):
        """There is no field a backend could put exception text into."""
        from dataclasses import fields

        assert "public_reason" not in {f.name for f in fields(UploadFailure)}
        failure = UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)
        assert failure.public_reason == FileUploadErrorCode.STORAGE_REJECTED.public_reason

    @pytest.mark.parametrize("code", list(FileUploadErrorCode))
    def test_every_code_has_a_public_reason(self, code):
        assert code.public_reason


class TestCompensationSeam:
    async def test_discard_delegates_to_the_backend(self):
        """Only the backend knows whether a stored file can be withdrawn."""
        seen: list[list[UploadReceipt]] = []

        class DeletingBackend(StubFileStorageBackend):
            async def discard(self, receipts):
                seen.append(list(receipts))

        manager = FileUploadManager(DeletingBackend())
        await manager.discard([UploadReceipt(uri="u-0", file_id="f-0")])

        assert [r.file_id for r in seen[0]] == ["f-0"]

    async def test_discard_of_nothing_does_not_reach_the_backend(self):
        class Exploding(StubFileStorageBackend):
            async def discard(self, receipts):
                raise AssertionError("must not be called")

        await FileUploadManager(Exploding()).discard([])

    async def test_backend_without_deletion_reports_orphans(self, caplog):
        """Saying so beats pretending the compensation happened."""
        from aion.server.files.storage.backends.base import FileStorageBackend

        class NoDeletion(FileStorageBackend):
            async def store_many(self, uploads, *, context):
                return []

        with caplog.at_level("WARNING"):
            await NoDeletion().discard([UploadReceipt(uri="u-0", file_id="f-0")])

        assert "f-0" in caplog.text

    async def test_orphan_report_never_logs_the_uri(self, caplog):
        """A storage service may answer with a signed URL that is a credential."""
        from aion.server.files.storage.backends.base import FileStorageBackend

        class NoDeletion(FileStorageBackend):
            async def store_many(self, uploads, *, context):
                return []

        secret = "https://cdn.test/f?signature=s3cr3t"
        with caplog.at_level("WARNING"):
            await NoDeletion().discard([UploadReceipt(uri=secret, file_id="f-0")])

        assert "s3cr3t" not in caplog.text
