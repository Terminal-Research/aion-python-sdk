"""A2A artifact backend: memory-first storage with DB fallback and TTL eviction."""

from __future__ import annotations
import logging

from typing_extensions import override

import asyncio
import dataclasses
import time
from a2a.types import Artifact
from aion.adk.authoring.transformers import convert_a2a_part_to_genai_part
from aion.core.db import DbManagerProtocol
from aion.core.exceptions import AionError
from aion.db.postgres.repositories.adk_artifact_versions import AdkArtifactVersionsRepository
from aion.db.postgres.repositories.tasks.repository import TasksRepository
from google.adk.artifacts import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ArtifactVersion, ensure_part
from google.adk.errors.input_validation_error import InputValidationError
from google.genai import types
from typing import Any, Optional, List, Union

from .base import ArtifactServiceBackend

logger = logging.getLogger(__name__)

VERSION_ATTEMPTS = 10
"""How many numbers one save tries: each conflict means another server gave the number meanwhile."""


class ArtifactVersionConflictError(AionError):
    """Every version number a save tried was given by another server meanwhile."""


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
    on the first write to an empty path, we ask DB which versions already
    exist and number from the next one, so new versions pick up where the
    last one left off. When memory is evicted, the offset is cleared so the
    next write re-syncs with DB.

    With a database, every number of a session-scoped artifact is also
    recorded as it is given (``adk_artifact_versions``), and the next number
    counts the recorded ones too. Several servers of an agent, or several
    processes, saving one artifact at once therefore never give one number
    twice: a number another server recorded first is refused, and the save
    numbers again from what the database holds, up to ``VERSION_ATTEMPTS``
    times. The versions one service holds in memory can then have gaps - the
    numbers other servers gave - so memory is searched by version number.

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
    def _mime_type_for(artifact: Union[types.Part, dict[str, Any]]) -> str:
        """Infers MIME type from the artifact part. Raises if the type is unsupported."""
        if artifact.inline_data is not None:
            return artifact.inline_data.mime_type
        if artifact.text is not None:
            return "text/plain"
        if artifact.file_data is not None:
            return artifact.file_data.mime_type
        raise InputValidationError("Not supported artifact type.")

    @staticmethod
    def _parse_versions_from_db(artifacts: List[Artifact]) -> list[int]:
        """Pulls integer version numbers out of DB artifact metadata."""
        versions = []
        for artifact in artifacts:
            if artifact.HasField("metadata") and "version" in artifact.metadata:
                try:
                    versions.append(int(artifact.metadata["version"]))
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
            artifact: Union[types.Part, dict[str, Any]],
            session_id: Optional[str] = None,
            custom_metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        """Appends a new version of the artifact to memory and returns its logical version number.

        On the first write to an empty path, queries DB for the next version
        number so logical versions stay continuous with what's in DB. With a
        database, the number is recorded before it is used (see the class
        docstring).

        Raises:
            ArtifactVersionConflictError: Other servers gave every number this
                save tried.
        """
        artifact = ensure_part(artifact)
        path = self._artifact_path(app_name, user_id, filename, session_id)

        if path not in self._version_offsets:
            if not self._artifacts.get(path) and session_id:
                self._version_offsets[path] = await self._resolve_db_version_offset(app_name=app_name, user_id=user_id, 
                    session_id=session_id, filename=filename
                )
            else:
                self._version_offsets[path] = 0

        entries = self._artifacts.setdefault(path, [])
        offset = self._version_offsets[path]
        logical_version = entries[-1].artifact_version.version + 1 if entries else offset
        if session_id and not self._file_has_user_namespace(filename) and self._is_db_available():
            logical_version = await self._record_version(
                app_name=app_name, user_id=user_id, session_id=session_id, filename=filename, version=logical_version
            )

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

    async def _record_version(
            self, *, app_name: str, user_id: str, session_id: str, filename: str, version: int
    ) -> int:
        """Record ``version`` as given, or the next number no other server gave first; the number recorded.

        When the database fails to answer, the number is used unrecorded:
        the service goes on from memory, as its reads do.

        Raises:
            ArtifactVersionConflictError: Other servers gave every number tried.
        """
        assert self._db_manager is not None
        for _ in range(VERSION_ATTEMPTS):
            try:
                async with self._db_manager.get_session() as db_session:
                    async with db_session.begin():
                        recorded = await AdkArtifactVersionsRepository(db_session).claim(
                            agent_id=app_name,
                            owner_scope=user_id,
                            context_id=session_id,
                            artifact_name=filename,
                            version=version,
                        )
            except Exception as e:
                logger.warning(f"Recording version {version} of '{filename}' failed: {e}", exc_info=True)
                return version
            if recorded:
                return version
            given = version
            version = max(
                version + 1,
                await self._resolve_db_version_offset(
                    app_name=app_name, user_id=user_id, session_id=session_id, filename=filename
                ),
            )
            logger.debug(f"Version {given} of '{filename}' was given elsewhere; trying {version}")
        raise ArtifactVersionConflictError(
            f"Version numbers of artifact '{filename}' in context {session_id} were given elsewhere "
            f"{VERSION_ATTEMPTS} times in a row"
        )

    @staticmethod
    def _entry(entries: list[_ArtifactEntry], version: int) -> Optional[_ArtifactEntry]:
        """The entry in memory holding ``version``, if this service saved it."""
        return next((entry for entry in entries if entry.artifact_version.version == version), None)

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
            return await self._load_from_db(app_name=app_name, user_id=user_id, 
                session_id=session_id, filename=filename, version=version
            )

        if version is None:
            entry = entries[-1]
        elif version < offset:
            return await self._load_from_db(app_name=app_name, user_id=user_id, 
                session_id=session_id, filename=filename, version=version
            )
        else:
            entry = self._entry(entries, version)
            if entry is None:
                return await self._load_from_db(app_name=app_name, user_id=user_id, 
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
            filenames.update(await self._fetch_db_artifact_keys(app_name=app_name, user_id=user_id, session_id=session_id))

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
            return sorted(set(range(offset)) | {entry.artifact_version.version for entry in entries})

        if not session_id:
            return []
        db_artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, 
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
            db_artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, 
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
        db_artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, 
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
                entry = self._entry(entries, version)
                if entry is not None:
                    return entry.artifact_version

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
            app_name: str,
            user_id: str,
            session_id: str,
            filename: str,
            version: Optional[int] = None,
            latest_only: bool = False,
    ) -> List[Artifact]:
        """Fetches artifact records from DB. Returns empty list on any failure.

        Only the rows of this agent and user are read: ``app_name`` is the Aion
        agent id and ``user_id`` the caller's ``owner_scope``, the two columns
        the tasks table scopes every row by, so another agent's or another
        user's artifacts under the same ``context_id`` never come back.

        ``latest_only`` is for a caller that only ever reads the newest
        version back out: it lets the repository stop at the first matching
        task row instead of loading the named artifact's full history. Ignored
        when ``version`` already pins a specific one - a caller asking for
        version 3 is not asking for the latest.
        """
        if not self._is_db_available():
            return []
        assert self._db_manager is not None
        try:
            if version is not None:
                artifact_version = str(version)
            elif latest_only:
                artifact_version = "-1"
            else:
                artifact_version = None
            async with self._db_manager.get_session() as db_session:
                repo = TasksRepository(db_session)
                return await repo.find_artifacts(
                    agent_id=app_name,
                    owner_scope=user_id,
                    context_id=session_id,
                    artifact_name=filename,
                    artifact_version=artifact_version,
                )
        except Exception as e:
            logger.warning(f"DB fetch failed for '{filename}': {e}", exc_info=True)
            return []

    async def _fetch_db_artifact_keys(self, *, app_name: str, user_id: str, session_id: str) -> list[str]:
        """Returns distinct artifact names stored in DB for this session."""
        if not self._is_db_available():
            return []
        assert self._db_manager is not None
        try:
            async with self._db_manager.get_session() as db_session:
                repo = TasksRepository(db_session)
                artifacts = await repo.find_artifacts(
                    agent_id=app_name, owner_scope=user_id, context_id=session_id
                )
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
        artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, 
            session_id=session_id, filename=filename, version=version, latest_only=True
        )
        if not artifacts:
            return None
        artifact = artifacts[0]
        logical_version = version
        if logical_version is None and artifact.HasField("metadata") and "version" in artifact.metadata:
            try:
                logical_version = int(artifact.metadata["version"])
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

    async def _resolve_db_version_offset(
            self, *, app_name: str, user_id: str, session_id: str, filename: str
    ) -> int:
        """Returns the next available version number based on what's already in DB.

        Counts the versions the tasks hold - 0 if there are no records, 1 if
        records exist but have no version metadata (treating them collectively
        as version 0), max(version) + 1 if version metadata is present - and
        the numbers recorded as given, which a task may not hold yet: the
        higher of the two.
        """
        artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, session_id=session_id, filename=filename)
        next_stored = 0
        if artifacts:
            versions = self._parse_versions_from_db(artifacts)
            next_stored = max(versions) + 1 if versions else 1
        latest_recorded = await self._fetch_latest_recorded_version(
            app_name=app_name, user_id=user_id, session_id=session_id, filename=filename
        )
        return max(next_stored, latest_recorded + 1) if latest_recorded is not None else next_stored

    async def _fetch_latest_recorded_version(
            self, *, app_name: str, user_id: str, session_id: str, filename: str
    ) -> Optional[int]:
        """The highest version number recorded as given, or None. None on any failure."""
        if not self._is_db_available():
            return None
        assert self._db_manager is not None
        try:
            async with self._db_manager.get_session() as db_session:
                return await AdkArtifactVersionsRepository(db_session).latest(
                    agent_id=app_name, owner_scope=user_id, context_id=session_id, artifact_name=filename
                )
        except Exception as e:
            logger.warning(f"DB fetch of recorded versions failed for '{filename}': {e}", exc_info=True)
            return None

    async def _load_from_db(
            self,
            *,
            app_name: str,
            user_id: str,
            session_id: Optional[str],
            filename: str,
            version: Optional[int],
    ) -> Optional[types.Part]:
        """Loads artifact data directly from DB and converts it to a genai Part."""
        if not session_id:
            return None
        artifacts = await self._fetch_db_artifacts(app_name=app_name, user_id=user_id, 
            session_id=session_id, filename=filename, version=version, latest_only=True
        )
        if not artifacts:
            return None
        artifact = artifacts[0]
        if not artifact.parts:
            return None
        return convert_a2a_part_to_genai_part(artifact.parts[0])

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
