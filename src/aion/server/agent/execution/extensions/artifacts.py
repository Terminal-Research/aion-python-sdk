"""The artifact versions of one turn: the number an executor's artifact is written under, and the lookup.

The same artifact id in several tasks of a context is one artifact in several
versions, numbered from 0 in ``metadata["version"]``. ``TurnArtifacts`` serves
one turn of one task, over ``ContextArtifacts``:

* ``number`` is the version a task writes an artifact id under. A task that
  already holds the artifact keeps its number, so a replacement or a later
  turn does not renumber it; otherwise the number follows the newest version
  in the context, or is 0 for the first.
* ``stamp`` writes that number into an artifact event the task is about to
  write (``metadata["version"]``), asking the store once per artifact id;
* ``find`` is the newest version in the context.

Both look only at the tasks of the context holder the turn's caller was
admitted as, and count with a query on every call rather than a cache, so any
server of the agent numbers from what the store holds.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import TaskArtifactUpdateEvent

from aion.core.a2a.artifacts import ARTIFACT_VERSION_KEY
from aion.server.tasks.admission import ContextHolder
from aion.server.tasks.context_artifacts import ContextArtifacts, ContextArtifactVersion

__all__ = ["TurnArtifacts"]


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
        self._numbers: dict[str, int] = {}

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

    async def stamp(self, event: TaskArtifactUpdateEvent) -> None:
        """Write the version the task writes the event's artifact under into its ``metadata["version"]``.

        Every chunk and replacement of an artifact id in the task carries the
        same number; the store is asked once per id.
        """
        artifact_id = event.artifact.artifact_id
        if not artifact_id:
            return
        if artifact_id not in self._numbers:
            self._numbers[artifact_id] = await self.number(artifact_id)
        event.artifact.metadata[ARTIFACT_VERSION_KEY] = str(self._numbers[artifact_id])

    async def find(self, artifact_id: str) -> Optional[ContextArtifactVersion]:
        """The newest version of ``artifact_id`` in the context, or None."""
        versions = await self._versions(artifact_id)
        return versions[0] if versions else None

    async def _versions(self, artifact_id: str) -> list[ContextArtifactVersion]:
        if self._holder is None:
            return []
        return await self._context_artifacts.versions(self._context_id, artifact_id, self._holder)
