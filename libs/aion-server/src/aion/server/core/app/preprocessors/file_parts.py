"""File part preprocessor: replaces inline base64 parts with URI parts."""

from typing import Any

from a2a.types import SendMessageRequest, SendStreamingMessageRequest
from aion.shared.files.storage import FilePartTransformer


class FilePartPreprocessor:
    """Transforms inline FilePart(FileWithBytes) > FilePart(FileWithUri) in incoming requests.

    Runs before any handler or task store operation, ensuring that neither
    the task saved to DB nor the agent receives raw base64 bytes.

    Upload is awaited (drain) before returning — at this point in the pipeline
    no outgoing background uploads are in flight, so drain waits exclusively
    for the uploads triggered by this request.
    """

    def __init__(self, file_transformer: FilePartTransformer) -> None:
        self._transformer = file_transformer

    async def preprocess(self, request_obj: Any) -> None:
        if not isinstance(request_obj, (SendMessageRequest, SendStreamingMessageRequest)):
            return

        params = request_obj.params
        if not params or not params.message:
            return

        transformed = await self._transformer.transform_message(params.message)
        if transformed is not params.message:
            params.message = transformed

        await self._transformer.drain()
