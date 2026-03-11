"""Abstract interface for file storage backends.

Storage backends are responsible for persisting binary content and providing
a stable URL before the upload completes, enabling the presigned-URL pattern.
"""

from abc import ABC, abstractmethod


class FileStorageBackend(ABC):
    """Abstract file storage service.

    Implementations must support the presigned-URL pattern:
    - generate_uri() returns the final URL for a file_id BEFORE upload starts
    - upload() persists the actual bytes asynchronously

    This decoupling allows the server to send the URL to the client
    immediately while the upload happens in the background.
    """

    @abstractmethod
    def generate_uri(
        self,
        mime_type: str | None = None,
        context_id: str | None = None,
    ) -> tuple[str, str]:
        """Generate a file_id and return (file_id, uri) synchronously.

        Called before upload() to obtain the final URL immediately, so it can
        be sent to the client while the upload happens in the background.

        File is stored under {context_id}/{file_id}.{ext} - unique by UUID,
        extension derived from mime_type for readability.

        Args:
            mime_type: MIME type of the content.
            context_id: Session/conversation identifier.

        Returns:
            A (file_id, uri) tuple. file_id is used in the subsequent upload()
            call. uri is the publicly accessible URL that will serve the file.
        """

    @abstractmethod
    async def upload(
        self,
        file_id: str,
        data: bytes,
        mime_type: str,
        context_id: str | None = None,
    ) -> None:
        """Upload file bytes to storage.

        Called in the background after generate_uri(). Failures are logged
        but do not affect the event already sent to the client.

        Args:
            file_id: The file_id returned by generate_uri().
            data: Raw file bytes.
            mime_type: MIME type of the content.
            context_id: Session/conversation identifier.
        """
