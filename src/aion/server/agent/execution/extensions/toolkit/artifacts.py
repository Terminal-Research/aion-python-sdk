"""The artifact versions of one turn: the number a toolkit's artifact is written under, and the lookup.

The same artifact id in several tasks of a context is one artifact in several
versions, numbered from 0 in ``metadata["version"]``. ``TurnArtifacts`` serves
one turn of one task, over ``ContextArtifacts``:

* ``number`` is the version a task writes an artifact id under. A task that
  already holds the artifact keeps its number, so a replacement or a later
  turn does not renumber it; otherwise the number follows the newest version
  in the context, or is 0 for the first.
* ``find`` is the toolkit's ``find_artifact``: the newest version in the
  context, in the protocol's own artifact model.

Both look only at the tasks of the context holder the turn's caller was
admitted as, and count with a query on every call rather than a cache, so any
server of the agent numbers from what the store holds.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Optional

from a2a.types import Part as A2APart
from google.protobuf import json_format
from pydantic import ValidationError

from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1
from aion.core.toolkit import (
    ARTIFACT_VERSION_KEY,
    Artifact,
    ContextArtifact,
    DataPart,
    FilePart,
    Part,
    TextPart,
)
from aion.server.tasks.admission import ContextHolder
from aion.server.tasks.context_artifacts import ContextArtifacts, ContextArtifactVersion

__all__ = ["TurnArtifacts", "context_artifact"]

logger = logging.getLogger(__name__)

_RESERVED_PREFIXES = ("aion:", "https://docs.aion.to/a2a/extensions/")


class TurnArtifacts:
    """Numbers the artifacts one turn writes and finds the context's, for one holder.

    Args:
        context_artifacts: Reads the versions among the tasks of a context.
        context_id: The task's context.
        task_id: The task being served.
        holder: The context holder the turn's caller was admitted as; None
            when the caller has none, which sees no versions.
    """

    def __init__(
            self,
            context_artifacts: ContextArtifacts,
            *,
            context_id: str,
            task_id: str,
            holder: Optional[ContextHolder],
    ) -> None:
        self._context_artifacts = context_artifacts
        self._context_id = context_id
        self._task_id = task_id
        self._holder = holder

    async def number(self, artifact_id: str) -> int:
        """The version number this task writes ``artifact_id`` under.

        Returns:
            The number the task already holds the artifact under; otherwise one
            more than the newest version in the context, or 0 when there is none.
        """
        versions = await self._versions(artifact_id)
        for version in versions:
            if version.task_id == self._task_id:
                return version.version
        return versions[0].version + 1 if versions else 0

    async def find(self, artifact_id: str) -> Optional[ContextArtifact]:
        """The newest version of ``artifact_id`` in the context, or None.

        None as well for a stored artifact the protocol's model cannot hold
        (a foreign artifact that happens to share the id); that is logged.
        """
        versions = await self._versions(artifact_id)
        if not versions:
            return None
        try:
            return context_artifact(versions[0])
        except ValidationError as error:
            logger.warning(
                "The newest artifact %s of context %s cannot be read as a toolkit artifact: %s",
                artifact_id,
                self._context_id,
                error,
            )
            return None

    async def _versions(self, artifact_id: str) -> list[ContextArtifactVersion]:
        if self._holder is None:
            return []
        return await self._context_artifacts.versions(self._context_id, artifact_id, self._holder)


def context_artifact(stored: ContextArtifactVersion) -> ContextArtifact:
    """A stored version as the protocol's :class:`ContextArtifact`.

    The version number moves from the artifact's metadata to the field, and
    metadata keys the server reserves are left out, since the protocol's model
    refuses both. A data part that is not an object travels as ``{"value": ...}``.

    Raises:
        pydantic.ValidationError: The stored artifact has no part the protocol
            can hold.
    """
    artifact = stored.artifact
    stored_metadata = json_format.MessageToDict(artifact.metadata) if artifact.HasField("metadata") else {}
    metadata = _toolkit_keys(stored_metadata)
    metadata.pop(ARTIFACT_VERSION_KEY, None)
    parts: list[Part] = [built for part in artifact.parts if (built := _part(part)) is not None]
    protocol_artifact = Artifact(
        id=artifact.artifact_id,
        name=artifact.name or None,
        description=artifact.description or None,
        parts=parts,
        metadata=metadata,
    )
    return ContextArtifact(
        artifact=protocol_artifact,
        task_id=stored.task_id,
        version=stored.version,
        updated_at=stored.updated_at,
    )


def _part(part: A2APart) -> Optional[Part]:
    """The protocol part for a stored one; None for a part with no content."""
    kind = part.WhichOneof("content")
    stored = json_format.MessageToDict(part.metadata) if part.HasField("metadata") else {}
    metadata = _toolkit_keys(stored)
    if kind == "text":
        return TextPart(text=part.text, metadata=metadata)
    if kind == "data":
        data = json_format.MessageToDict(part.data)
        markup = stored.get(EVENT_EXTENSION_URI_V1)
        schema = markup.get("schema") if isinstance(markup, dict) else None
        return DataPart(
            data=data if isinstance(data, dict) else {"value": data},
            schema=schema,
            metadata=metadata,
        )
    if kind in ("raw", "url"):
        return FilePart(
            raw=part.raw if kind == "raw" else None,
            url=part.url if kind == "url" else None,
            filename=part.filename or None,
            media_type=part.media_type or None,
            metadata=metadata,
        )
    return None


def _toolkit_keys(stored: Mapping[str, Any]) -> dict[str, Any]:
    """Stored metadata without the keys the protocol reserves for the server."""
    return {key: value for key, value in stored.items() if not key.startswith(_RESERVED_PREFIXES)}
