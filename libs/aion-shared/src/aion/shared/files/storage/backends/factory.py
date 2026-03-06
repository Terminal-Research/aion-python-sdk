"""Factory for creating file storage backends from configuration."""

from aion.shared.files.storage.backends.base import FileStorageBackend
from aion.shared.files.storage.backends.stub import StubFileStorageBackend


class StorageBackendFactory:
    """Creates FileStorageBackend instances from config or env settings.

    Supports two usage patterns:

    1. From environment (AION_FILE_STORAGE_BACKEND):
        backend = StorageBackendFactory.from_settings()

    2. Explicit type:
        backend = StorageBackendFactory.create("stub")

    Returns None when no backend is configured — inline parts are passed
    through unchanged (base64 preserved).
    """

    @staticmethod
    def create(backend_type: str) -> FileStorageBackend:
        """Create a backend by type name.

        Args:
            backend_type: One of the supported backend identifiers.

        Raises:
            ValueError: If backend_type is not recognized.
        """
        match backend_type:
            case "stub":
                return StubFileStorageBackend()
            case _:
                raise ValueError(
                    f"Unknown storage backend type: '{backend_type}'. "
                    f"Available: 'stub'."
                )

    @classmethod
    def from_settings(cls) -> FileStorageBackend | None:
        """Create a backend from AION_FILE_STORAGE_BACKEND env var.

        Returns None if AION_FILE_STORAGE_BACKEND is not set, which disables
        inline-to-URL conversion entirely.
        """
        from aion.shared.settings import app_settings

        if not app_settings.file_storage_backend:
            return None

        return cls.create(app_settings.file_storage_backend)
