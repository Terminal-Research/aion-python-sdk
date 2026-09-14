"""File part preprocessor: stores inline content before anything persists it."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from a2a.types import SendMessageRequest, SubscribeToTaskRequest
from a2a.utils.errors import InternalError, InvalidParamsError
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import (
    UploadFailure,
    UploadReceipt,
    resolve_upload_context,
)

from .base import PreprocessingContext

logger = logging.getLogger(__name__)

_request_receipts: ContextVar[list[UploadReceipt]] = ContextVar(
    "_request_receipts", default=[]
)


class FilePartPreprocessor:
    """Replaces inline file parts with URL parts on the way in.

    Runs before any handler or task store operation, so neither the stored task
    nor the agent ever sees raw bytes. An inbound file that cannot be stored
    rejects the request instead of degrading: nothing was saved, and the client
    can simply send it again.

    Receipts produced during ``process`` are tracked per async context, so
    concurrent requests compensate only their own uploads.
    """

    def __init__(self, file_transformer: A2AFileTransformer) -> None:
        """Initialize the preprocessor.

        Args:
            file_transformer: Transformer that performs the conversion.
        """
        self._transformer = file_transformer

    async def process(self, request_obj: Any, context: PreprocessingContext) -> None:
        """Store inline content carried by the request, or reject it.

        Args:
            request_obj: Parsed A2A request; anything but a message-bearing
                request is ignored.
            context: Verified projection the upload is authorized against.

        Raises:
            InvalidParamsError: The request carries inline content that cannot
                be stored for a reason the client can fix - no declared
                distribution, an ambiguous organization, content the storage
                service rejects outright.
            InternalError: Storage was reachable but did not accept the
                content in time. Retrying the same request can succeed.
        """
        _request_receipts.set([])

        if not isinstance(request_obj, (SendMessageRequest, SubscribeToTaskRequest)):
            return

        if not request_obj.message or not self._transformer.upload_manager:
            return

        message = request_obj.message
        resolution = resolve_upload_context(
            context.extensions,
            context_id=message.context_id or None,
            task_id=message.task_id or None,
        )

        transform = await self._transformer.transform_message(
            message, upload_context=resolution
        )
        # Recorded before the rejection check: a batch can be half-stored when
        # one file fails, and those receipts still need compensating.
        _request_receipts.set(list(transform.report.receipts))

        if transform.report.failures:
            await self._compensate()
            raise _rejection(transform.report.failures)

        if not transform.report.changed:
            return

        request_obj.message.CopyFrom(transform.message)

    async def rollback(self) -> None:
        """Compensate uploads made for a request that was rejected afterwards."""
        await self._compensate()

    async def _compensate(self) -> None:
        """Withdraw what this request stored, never raising on the way.

        Compensation is secondary to the failure that triggered it, in both
        directions. Letting it raise out of ``process`` would replace the
        rejection the client needs to read - "no distribution declared" - with
        whatever the storage service said about a delete it also could not do;
        letting it raise out of ``rollback`` would mask the original downstream
        error. Either way the abandoned files are the smaller problem, and the
        traceback is logged where it happened.
        """
        receipts = _request_receipts.get()
        if not receipts:
            return
        _request_receipts.set([])

        upload_manager = self._transformer.upload_manager
        if upload_manager is None:
            return
        try:
            await upload_manager.discard(receipts)
        except Exception:  # noqa: BLE001 - must not displace the real failure
            logger.exception(
                "Could not compensate %d stored file(s) for a rejected request",
                len(receipts),
            )


def _rejection(failures: list[UploadFailure]) -> Exception:
    """Map a request's upload failures onto the A2A error class reporting them.

    Fault, not retryability, decides the class. The two are different
    questions: refused agent credentials and a server on its way down are
    neither retryable nor the sender's doing, and calling them invalid params
    would send the client hunting for a mistake it did not make.

    Across a batch, one failure the client can fix settles it: resending the
    request unchanged cannot succeed while that file is still in it, however
    transient the other failures were. Chosen by fault rather than by
    position, so the answer does not depend on the order the attachments
    happened to arrive in.

    Args:
        failures: Every failure recorded for the request; never empty.

    Returns:
        The error to raise.
    """
    reported = next((f for f in failures if f.client_fault), failures[0])
    logger.warning(
        "Rejecting a request carrying inline file content: %s (%d failure(s): %s)",
        reported.error_code.value,
        len(failures),
        ", ".join(sorted({f.error_code.value for f in failures})),
        exc_info=reported.cause,
    )
    if reported.client_fault:
        return InvalidParamsError(message=reported.public_reason)
    return InternalError(message=reported.public_reason)
