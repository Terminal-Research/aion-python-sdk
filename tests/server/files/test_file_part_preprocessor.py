"""Inbound path: storing attachments before anything can persist them.

Two rules are under test. Files are stored only after the request's extension
declarations are verified, because storing writes into the organization the
client named. And an inbound file that cannot be stored rejects the request
rather than degrading - nothing was saved, so the client can simply resend.
"""

from types import SimpleNamespace

import pytest
from a2a.types import Message, Part, Role, SendMessageRequest
from a2a.utils.errors import InternalError, InvalidParamsError
from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.core.runtime.context import AionRuntimeExtensions
from aion.server.core.app.handlers.request_preprocessors import (
    FilePartPreprocessor,
    PreprocessingContext,
)
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import (
    FileUploadErrorCode,
    FileUploadManager,
    StubFileStorageBackend,
    UploadFailure,
    UploadReceipt,
)

from .test_file_storage import (
    ORG,
    OutcomeBackend,
    RecordingBackend,
    distribution_payload,
    principal,
    raw_part,
)


def preprocessing_context(payload=None, carrier=None) -> PreprocessingContext:
    verified = {}
    if payload is not None:
        verified[DISTRIBUTION_EXTENSION_URI_V1] = payload
    if carrier is not None:
        from aion.core.constants import USAGE_ATTRIBUTION_EXTENSION_URI_V1

        verified[USAGE_ATTRIBUTION_EXTENSION_URI_V1] = carrier
    return PreprocessingContext(
        extensions=AionRuntimeExtensions(verified),
        call_context=SimpleNamespace(requested_extensions=set(), state={}),
    )


def request(*parts) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id="m-1",
            context_id="ctx-1",
            role=Role.ROLE_USER,
            parts=list(parts),
        )
    )


def preprocessor(backend=None) -> tuple[FilePartPreprocessor, object]:
    backend = backend or RecordingBackend()
    manager = FileUploadManager(backend)
    return FilePartPreprocessor(A2AFileTransformer(upload_manager=manager)), backend


class TestInboundStorage:
    async def test_inline_part_becomes_a_url_in_place(self):
        pre, _ = preprocessor()
        req = request(raw_part())

        await pre.process(req, preprocessing_context(distribution_payload(principal())))

        part = req.message.parts[0]
        assert part.url and not part.raw

    async def test_projection_reaches_the_backend(self):
        """Organization, selector and carrier arrive as explicit arguments.

        During preprocessing there is no runtime context for the storage client
        to read them from, so the projection is the only source.
        """
        pre, backend = preprocessor()

        await pre.process(
            request(raw_part()),
            preprocessing_context(distribution_payload(principal()), carrier="carrier-1"),
        )

        context = backend.contexts[0]
        assert context.organization_id == ORG
        assert context.usage_attribution == "carrier-1"
        assert context.principal_selector.to_header_value() == (
            "aion://agent/environment/env-1"
        )
        assert context.context_id == "ctx-1"

    async def test_text_only_request_without_distribution_passes(self):
        """Nothing to store, nothing to reject."""
        pre, backend = preprocessor()
        req = request(Part(text="hello"))

        await pre.process(req, preprocessing_context(None))

        assert req.message.parts[0].text == "hello"
        assert backend.batches == []

    async def test_inline_part_without_distribution_is_rejected(self):
        pre, backend = preprocessor()

        with pytest.raises(InvalidParamsError):
            await pre.process(request(raw_part()), preprocessing_context(None))

        assert backend.batches == []

    async def test_permanent_storage_rejection_is_a_client_error(self):
        backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)])
        pre, _ = preprocessor(backend)

        with pytest.raises(InvalidParamsError):
            await pre.process(
                request(raw_part()),
                preprocessing_context(distribution_payload(principal())),
            )

    async def test_retryable_storage_failure_is_a_server_error(self):
        """A client learns the same request is worth sending again."""
        backend = OutcomeBackend(
            [UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True)]
        )
        pre, _ = preprocessor(backend)

        with pytest.raises(InternalError):
            await pre.process(
                request(raw_part()),
                preprocessing_context(distribution_payload(principal())),
            )

    async def test_rejected_request_never_keeps_inline_bytes(self):
        backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)])
        pre, _ = preprocessor(backend)
        req = request(raw_part())

        with pytest.raises(InvalidParamsError):
            await pre.process(
                req, preprocessing_context(distribution_payload(principal()))
            )

        # The request object is untouched: it is rejected, never stored.
        assert req.message.parts[0].raw


class TestCompensation:
    async def test_partial_batch_failure_compensates_what_was_stored(self):
        """One bad file in five still rejects - the other four need compensating."""
        backend = OutcomeBackend(
            [
                UploadReceipt(uri="u-0", file_id="f-0"),
                UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
            ]
        )
        pre, _ = preprocessor(backend)
        discarded: list[list[UploadReceipt]] = []

        async def record(receipts):
            discarded.append(list(receipts))

        pre._transformer.upload_manager.discard = record

        with pytest.raises(InvalidParamsError):
            await pre.process(
                request(raw_part(name="a.png"), raw_part(name="b.png")),
                preprocessing_context(distribution_payload(principal())),
            )

        assert [r.uri for r in discarded[0]] == ["u-0"]

    async def test_rollback_after_a_later_failure_discards_receipts(self):
        pre, _ = preprocessor(StubFileStorageBackend())
        discarded: list[list[UploadReceipt]] = []

        async def record(receipts):
            discarded.append(list(receipts))

        pre._transformer.upload_manager.discard = record

        await pre.process(
            request(raw_part()), preprocessing_context(distribution_payload(principal()))
        )
        await pre.rollback()

        assert len(discarded[0]) == 1

    async def test_rollback_without_uploads_is_a_no_op(self):
        pre, _ = preprocessor()
        await pre.rollback()

    async def test_compensation_runs_once(self):
        """The handler rolls back the failing preprocessor too; it must be safe."""
        backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)])
        pre, _ = preprocessor(backend)
        calls: list[int] = []

        async def record(receipts):
            calls.append(len(list(receipts)))

        pre._transformer.upload_manager.discard = record

        with pytest.raises(InvalidParamsError):
            await pre.process(
                request(raw_part()),
                preprocessing_context(distribution_payload(principal())),
            )
        await pre.rollback()

        assert calls == []  # nothing was stored, so nothing to discard


class TestIgnoredRequests:
    async def test_other_request_types_are_ignored(self):
        pre, backend = preprocessor()
        await pre.process(object(), preprocessing_context(None))
        assert backend.batches == []

    async def test_inactive_transformer_is_a_no_op(self):
        pre = FilePartPreprocessor(A2AFileTransformer(upload_manager=None))
        req = request(raw_part())
        await pre.process(req, preprocessing_context(None))
        assert req.message.parts[0].raw


class TestRejectionClassification:
    """Fault, not retryability, picks the A2A error class.

    They answer different questions: whether the *same* request could work
    later, and whether the *sender* has anything to fix.
    """

    async def test_refused_credentials_are_not_the_senders_fault(self):
        """STORAGE_UNAUTHORIZED is permanent, but nothing the client can fix."""
        backend = OutcomeBackend(
            [UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED)]
        )
        pre, _ = preprocessor(backend)

        with pytest.raises(InternalError):
            await pre.process(
                request(raw_part()),
                preprocessing_context(distribution_payload(principal())),
            )

    async def test_shutdown_is_not_the_senders_fault(self):
        pre, _ = preprocessor()
        await pre._transformer.upload_manager.aclose()

        with pytest.raises(InternalError):
            await pre.process(
                request(raw_part()),
                preprocessing_context(distribution_payload(principal())),
            )

    async def test_a_client_fault_anywhere_in_the_batch_settles_it(self):
        """Resending unchanged cannot work while that one file is still in it."""
        backend = OutcomeBackend(
            [
                UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True),
                UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
            ]
        )
        pre, _ = preprocessor(backend)

        with pytest.raises(InvalidParamsError):
            await pre.process(
                request(raw_part(name="a.png"), raw_part(name="b.png")),
                preprocessing_context(distribution_payload(principal())),
            )

    async def test_classification_does_not_depend_on_part_order(self):
        """Same failures, opposite order, same answer."""
        failures = [
            UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
            UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True),
        ]
        for ordering in (failures, list(reversed(failures))):
            pre, _ = preprocessor(OutcomeBackend(ordering))
            with pytest.raises(InvalidParamsError):
                await pre.process(
                    request(raw_part(name="a.png"), raw_part(name="b.png")),
                    preprocessing_context(distribution_payload(principal())),
                )

    async def test_an_all_server_batch_stays_a_server_error(self):
        backend = OutcomeBackend(
            [
                UploadFailure(FileUploadErrorCode.STORAGE_UNAVAILABLE, retryable=True),
                UploadFailure(FileUploadErrorCode.STORAGE_UNAUTHORIZED),
            ]
        )
        pre, _ = preprocessor(backend)

        with pytest.raises(InternalError):
            await pre.process(
                request(raw_part(name="a.png"), raw_part(name="b.png")),
                preprocessing_context(distribution_payload(principal())),
            )


class TestFailingCompensation:
    """A failed withdrawal must not displace the failure that caused it.

    The abandoned files are the smaller problem: what the caller has to read is
    why the request was refused, or what went wrong downstream.
    """

    @staticmethod
    def _exploding(pre) -> None:
        async def boom(receipts):
            raise RuntimeError("delete unavailable")

        pre._transformer.upload_manager.discard = boom

    async def test_rejection_survives_a_failing_discard(self, caplog):
        backend = OutcomeBackend(
            [
                UploadReceipt(uri="u-0", file_id="f-0"),
                UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
            ]
        )
        pre, _ = preprocessor(backend)
        self._exploding(pre)

        with caplog.at_level("ERROR"):
            with pytest.raises(InvalidParamsError) as error:
                await pre.process(
                    request(raw_part(name="a.png"), raw_part(name="b.png")),
                    preprocessing_context(distribution_payload(principal())),
                )

        assert "storage service rejected" in str(error.value)
        assert "delete unavailable" in caplog.text

    async def test_rollback_swallows_a_failing_discard(self):
        """Its caller is unwinding a downstream error; this must not replace it."""
        pre, _ = preprocessor(StubFileStorageBackend())

        await pre.process(
            request(raw_part()),
            preprocessing_context(distribution_payload(principal())),
        )
        self._exploding(pre)

        await pre.rollback()

    async def test_a_failed_compensation_is_not_retried(self):
        """Receipts are released before the attempt, so rollback cannot loop."""
        attempts: list[int] = []

        pre, _ = preprocessor(
            OutcomeBackend(
                [
                    UploadReceipt(uri="u-0", file_id="f-0"),
                    UploadFailure(FileUploadErrorCode.STORAGE_REJECTED),
                ]
            )
        )

        async def boom(receipts):
            attempts.append(len(list(receipts)))
            raise RuntimeError("delete unavailable")

        pre._transformer.upload_manager.discard = boom

        with pytest.raises(InvalidParamsError):
            await pre.process(
                request(raw_part(name="a.png"), raw_part(name="b.png")),
                preprocessing_context(distribution_payload(principal())),
            )
        await pre.rollback()

        assert attempts == [1]
