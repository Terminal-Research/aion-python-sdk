"""A2A artifact backend: memory-first storage with DB fallback and TTL eviction."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Optional

from google.adk.artifacts import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ArtifactVersion
from aion.adk.transformers.utils import a2a_part_to_genai_part
from google.adk.errors.input_validation_error import InputValidationError
from google.genai import types
from typing_extensions import override

from aion.db.postgres.repositories.tasks.repository import TasksRepository
from aion.shared.db import DbManagerProtocol
from aion.shared.logging import get_logger

from .base import ArtifactServiceBackend

logger = get_logger()


@dataclasses.dataclass
class _ArtifactEntry:
    data: types.Part
    artifact_version: ArtifactVersion


class A2AArtifactService(BaseArtifactService):
    """Memory-first artifact service backed by DB, with TTL-based eviction.

    Keeps recently written artifacts in memory for fast access, while using
    the database as a source of truth for anything that's been evicted or
    existed before this service started.

    Version numbering stays continuous across restarts and eviction cycles:
    on the first write to an empty path, we ask DB how many versions already
    exist and use that count as an offset, so new versions pick up where the
    last one left off. When memory is evicted, the offset is cleared so the
    next write re-syncs with DB.

    ADK's session_id maps to context_id on the DB side. Versions are zero-based
    integers and stored as strings in artifact metadata.
    """

    def __init__(
        self,
        db_manager: Optional[DbManagerProtocol] = None,
        ttl: int = 300,
        cleanup_interval: int = 60,
    ):
        """
        Args:
            db_manager: Optional DB connection manager for persistence and fallback reads.
            ttl: Seconds before an artifact path is evicted from memory.
            cleanup_interval: How often (in seconds) the background eviction loop runs.
        """
        self._db_manager = db_manager
        self._ttl = ttl
        self._cleanup_interval = cleanup_interval
        self._artifacts: dict[str, list[_ArtifactEntry]] = {}
        self._timestamps: dict[str, float] = {}
        self._version_offsets: dict[str, int] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    @staticmethod
    def _file_has_user_namespace(filename: str) -> bool:
        """Returns True if the filename belongs to the user-scoped namespace."""
        return filename.startswith("user:")

    def _artifact_path(
        self,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str],
    ) -> str:
        """Builds the internal storage key for an artifact.

        User-scoped artifacts omit the session segment; session-scoped ones require session_id.
        """
        if self._file_has_user_namespace(filename):
            return f"{app_name}/{user_id}/user/{filename}"
        if session_id is None:
            raise InputValidationError(
                "Session ID must be provided for session-scoped artifacts."
            )
        return f"{app_name}/{user_id}/{session_id}/{filename}"

    def _canonical_uri_for(
        self,
        app_name: str,
        user_id: str,
        session_id: Optional[str],
        filename: str,
        version: int,
    ) -> str:
        """Builds a canonical memory:// URI for a specific artifact version."""
        base = f"memory://apps/{app_name}/users/{user_id}"
        if self._file_has_user_namespace(filename):
            return f"{base}/artifacts/{filename}/versions/{version}"
        return f"{base}/sessions/{session_id}/artifacts/{filename}/versions/{version}"

    @staticmethod
    def _mime_type_for(artifact: types.Part) -> str:
        """Infers MIME type from the artifact part. Raises if the type is unsupported."""
        if artifact.inline_data is not None:
            return artifact.inline_data.mime_type
        if artifact.text is not None:
            return "text/plain"
        if artifact.file_data is not None:
            return artifact.file_data.mime_type
        raise InputValidationError("Not supported artifact type.")

    @staticmethod
    def _parse_versions_from_db(artifacts: list) -> list[int]:
        """Pulls integer version numbers out of DB artifact metadata."""
        versions = []
        for a in artifacts:
            if a.metadata and (v := a.metadata.get("version")) is not None:
                try:
                    versions.append(int(v))
                except (ValueError, TypeError):
                    pass
        return versions

    @override
    async def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        artifact: types.Part,
        session_id: Optional[str] = None,
        custom_metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        """Appends a new version of the artifact to memory and returns its logical version number.

        On the first write to an empty path, queries DB for the current version count
        and uses it as an offset so logical versions stay continuous with what's in DB.
        """
        path = self._artifact_path(app_name, user_id, filename, session_id)

        if path not in self._version_offsets:
            if not self._artifacts.get(path) and session_id:
                self._version_offsets[path] = await self._resolve_db_version_offset(
                    session_id=session_id, filename=filename
                )
            else:
                self._version_offsets[path] = 0

        entries = self._artifacts.setdefault(path, [])
        offset = self._version_offsets[path]
        logical_version = len(entries) + offset

        artifact_version = ArtifactVersion(
            version=logical_version,
            canonical_uri=self._canonical_uri_for(
                app_name, user_id, session_id, filename, logical_version
            ),
        )
        if custom_metadata:
            artifact_version.custom_metadata = custom_metadata
        artifact_version.mime_type = self._mime_type_for(artifact)

        entries.append(_ArtifactEntry(data=artifact, artifact_version=artifact_version))
        self._timestamps[path] = time.monotonic()
        self._ensure_cleanup_task()
        return logical_version

    @override
    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[types.Part]:
        """Returns the artifact data for the given version (or latest if version is None).

        Serves from memory when possible; falls back to DB for evicted or pre-offset versions.
        Returns None for empty/placeholder artifacts.
        """
        path = self._artifact_path(app_name, user_id, filename, session_id)
        entries = self._artifacts.get(path)
        offset = self._version_offsets.get(path, 0)

        if not entries:
            return await self._load_from_db(
                session_id=session_id, filename=filename, version=version
            )

        if version is None:
            entry = entries[-1]
        elif version < offset:
            return await self._load_from_db(
                session_id=session_id, filename=filename, version=version
            )
        else:
            mem_index = version - offset
            try:
                entry = entries[mem_index]
            except IndexError:
                return await self._load_from_db(
                    session_id=session_id, filename=filename, version=version
                )

        data = entry.data
        if (
            data == types.Part()
            or data == types.Part(text="")
            or (data.inline_data and not data.inline_data.data)
        ):
            return None
        return data

    @override
    async def list_artifact_keys(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> list[str]:
        """Returns sorted artifact filenames visible in this session, merging memory and DB."""
        usernamespace_prefix = f"{app_name}/{user_id}/user/"
        session_prefix = f"{app_name}/{user_id}/{session_id}/" if session_id else None
        filenames: set[str] = set()
        for path in self._artifacts:
            if session_prefix and path.startswith(session_prefix):
                filenames.add(path.removeprefix(session_prefix))
            elif path.startswith(usernamespace_prefix):
                filenames.add(path.removeprefix(usernamespace_prefix))

        if session_id:
            filenames.update(await self._fetch_db_artifact_keys(session_id=session_id))

        return sorted(filenames)

    @override
    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Removes all in-memory state for an artifact path (data, timestamps, and offset)."""
        path = self._artifact_path(app_name, user_id, filename, session_id)
        self._artifacts.pop(path, None)
        self._timestamps.pop(path, None)
        self._version_offsets.pop(path, None)

    @override
    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[int]:
        """Returns all known version numbers for an artifact, including pre-offset DB versions."""
        path = self._artifact_path(app_name, user_id, filename, session_id)
        entries = self._artifacts.get(path)
        if entries:
            offset = self._version_offsets.get(path, 0)
            return list(range(0, offset + len(entries)))

        if not session_id:
            return []
        db_artifacts = await self._fetch_db_artifacts(
            session_id=session_id, filename=filename
        )
        return sorted(self._parse_versions_from_db(db_artifacts))

    @override
    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[ArtifactVersion]:
        """Returns ArtifactVersion objects for all versions, merging DB (pre-offset) and memory."""
        path = self._artifact_path(app_name, user_id, filename, session_id)
        entries = self._artifacts.get(path)
        offset = self._version_offsets.get(path, 0)
        mem_versions = [entry.artifact_version for entry in entries] if entries else []

        if offset > 0 and session_id:
            db_artifacts = await self._fetch_db_artifacts(
                session_id=session_id, filename=filename
            )
            db_versions = [
                ArtifactVersion(
                    version=v,
                    canonical_uri=self._canonical_uri_for(
                        app_name, user_id, session_id, filename, v
                    ),
                )
                for v in sorted(self._parse_versions_from_db(db_artifacts))
                if v < offset
            ]
            return db_versions + mem_versions

        if mem_versions:
            return mem_versions

        if not session_id:
            return []
        db_artifacts = await self._fetch_db_artifacts(
            session_id=session_id, filename=filename
        )
        return [
            ArtifactVersion(
                version=v,
                canonical_uri=self._canonical_uri_for(
                    app_name, user_id, session_id, filename, v
                ),
            )
            for v in sorted(self._parse_versions_from_db(db_artifacts))
        ]

    @override
    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        """Returns the ArtifactVersion metadata for the requested version (latest if None).

        Checks memory first; falls back to DB for versions outside the in-memory range.
        """
        path = self._artifact_path(app_name, user_id, filename, session_id)
        entries = self._artifacts.get(path)
        offset = self._version_offsets.get(path, 0)

        if entries:
            if version is None:
                return entries[-1].artifact_version
            if version >= offset:
                try:
                    return entries[version - offset].artifact_version
                except IndexError:
                    pass

        return await self._build_artifact_version_from_db(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            version=version,
        )

    def _is_db_available(self) -> bool:
        """Returns True if a DB manager is configured and its connection is initialized."""
        return bool(self._db_manager and self._db_manager.is_initialized)

    async def _fetch_db_artifacts(
        self,
        *,
        session_id: str,
        filename: str,
        version: Optional[int] = None,
    ) -> list:
        """Fetches artifact records from DB. Returns empty list on any failure."""
        if not self._is_db_available():
            return []
        assert self._db_manager is not None
        try:
            artifact_version = str(version) if version is not None else None
            async with self._db_manager.get_session() as db_session:
                repo = TasksRepository(db_session)
                return await repo.find_artifacts(
                    context_id=session_id,
                    artifact_name=filename,
                    artifact_version=artifact_version,
                )
        except Exception as e:
            logger.warning(f"DB fetch failed for '{filename}': {e}")
            return []

    async def _fetch_db_artifact_keys(self, *, session_id: str) -> list[str]:
        """Returns distinct artifact names stored in DB for this session."""
        if not self._is_db_available():
            return []
        assert self._db_manager is not None
        try:
            async with self._db_manager.get_session() as db_session:
                repo = TasksRepository(db_session)
                artifacts = await repo.find_artifacts(context_id=session_id)
            return [a.name for a in artifacts if a.name]
        except Exception as e:
            logger.warning(f"DB fetch artifact keys failed for session '{session_id}': {e}")
            return []

    async def _build_artifact_version_from_db(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: Optional[str],
        filename: str,
        version: Optional[int],
    ) -> Optional[ArtifactVersion]:
        """Constructs an ArtifactVersion from a DB record (no in-memory entry required)."""
        if not session_id:
            return None
        artifacts = await self._fetch_db_artifacts(
            session_id=session_id, filename=filename, version=version
        )
        if not artifacts:
            return None
        a = artifacts[0]
        logical_version = version
        if logical_version is None and a.metadata:
            try:
                logical_version = int(a.metadata["version"])
            except (KeyError, ValueError, TypeError):
                logical_version = 0
        if logical_version is None:
            logical_version = 0

        return ArtifactVersion(
            version=logical_version,
            canonical_uri=self._canonical_uri_for(
                app_name, user_id, session_id, filename, logical_version
            ),
        )

    async def _resolve_db_version_offset(self, *, session_id: str, filename: str) -> int:
        """Returns the next available version number based on what's already in DB.

        Returns 0 if there are no existing records, 1 if records exist but
        have no version metadata (treating them collectively as version 0),
        or max(version) + 1 if version metadata is present.
        """
        artifacts = await self._fetch_db_artifacts(session_id=session_id, filename=filename)
        if not artifacts:
            return 0
        versions = self._parse_versions_from_db(artifacts)
        return max(versions) + 1 if versions else 1

    async def _load_from_db(
        self,
        *,
        session_id: Optional[str],
        filename: str,
        version: Optional[int],
    ) -> Optional[types.Part]:
        """Loads artifact data directly from DB and converts it to a genai Part."""
        if not session_id:
            return None
        artifacts = await self._fetch_db_artifacts(
            session_id=session_id, filename=filename, version=version
        )
        if not artifacts:
            return None
        artifact = artifacts[0]
        if not artifact.parts:
            return None
        return a2a_part_to_genai_part(artifact.parts[0])

    def _ensure_cleanup_task(self) -> None:
        """Starts the background eviction loop if it's not already running."""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._cleanup_loop())
            except RuntimeError:
                pass

    async def _cleanup_loop(self) -> None:
        """Runs forever, triggering eviction every cleanup_interval seconds."""
        while True:
            await asyncio.sleep(self._cleanup_interval)
            await self._evict_expired()

    async def _evict_expired(self) -> None:
        """Drops all artifact paths whose last write timestamp exceeds the TTL."""
        now = time.monotonic()
        expired = [
            path
            for path, ts in list(self._timestamps.items())
            if now - ts > self._ttl
        ]
        for path in expired:
            self._artifacts.pop(path, None)
            self._timestamps.pop(path, None)
            self._version_offsets.pop(path, None)
            logger.debug(f"Evicted artifact from memory: {path}")

    async def close(self) -> None:
        """Stop the background eviction task. Call on shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass


class A2ABackend(ArtifactServiceBackend):
    """Backend that creates A2AArtifactService with optional DB fallback and TTL eviction."""

    def __init__(
        self,
        db_manager: Optional[DbManagerProtocol] = None,
        ttl: int = 300,
        cleanup_interval: int = 60,
    ):
        self._db_manager = db_manager
        self._ttl = ttl
        self._cleanup_interval = cleanup_interval

    def create(self) -> A2AArtifactService:
        """Instantiates a new A2AArtifactService with the configured DB manager and TTL."""
        return A2AArtifactService(
            db_manager=self._db_manager,
            ttl=self._ttl,
            cleanup_interval=self._cleanup_interval,
        )

    def is_available(self) -> bool:
        """Always available — no external dependencies required at creation time."""
        return True


__all__ = ["A2ABackend", "A2AArtifactService"]
