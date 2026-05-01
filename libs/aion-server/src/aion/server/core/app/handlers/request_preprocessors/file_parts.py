"""File part preprocessor: replaces inline base64 parts with URI parts."""

from contextvars import ContextVar
from typing import Any

from a2a.types import SendMessageRequest, SubscribeToTaskRequest
from aion.shared.files.a2a import A2AFileTransformer
from aion.shared.logging import get_logger

logger = get_logger()

_request_uris: ContextVar[list[str]] = ContextVar('_request_uris', default=[])


class FilePartPreprocessor:
    """Transforms inline FilePart(FileWithBytes) > FilePart(FileWithUri) in incoming requests.

    Runs before any handler or task store operation, ensuring that neither
    the task saved to DB nor the agent receives raw base64 bytes.

    URIs uploaded during preprocess() are tracked per async context via ContextVar.
    On rollback(), pending uploads are cancelled and completed files are deleted.
    """

    def __init__(self, file_transformer: A2AFileTransformer, *, wait_upload: bool = True) -> None:
        self._transformer = file_transformer
        self._wait_upload = wait_upload

    async def process(self, request_obj: Any) -> None:
        _request_uris.set([])

        if not isinstance(request_obj, (SendMessageRequest, SubscribeToTaskRequest)):
            return

        if not request_obj.message or not self._transformer.upload_manager:
            return

        upload_manager = self._transformer.upload_manager
        uris_before = set(upload_manager._pending)

        transformed_message = await self._transformer.transform_message(
            request_obj.message, wait_upload=False
        )

        if transformed_message is request_obj.message:
            return

        new_uris = list(set(upload_manager._pending) - uris_before)
        _request_uris.set(new_uris)

        if self._wait_upload:
            await upload_manager.wait(new_uris)

        request_obj.message.CopyFrom(transformed_message)

    async def rollback(self) -> None:
        uris = _request_uris.get()
        if not uris or not self._transformer.upload_manager:
            return

        upload_manager = self._transformer.upload_manager
        for uri in uris:
            await upload_manager.delete(uri)
            logger.debug("[FilePartPreprocessor] rolled back upload: uri=%s", uri)

        _request_uris.set([])
