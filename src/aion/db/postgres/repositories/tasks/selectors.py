"""Artifact selectors — pure Python post-processing of DB query results."""

from __future__ import annotations

from typing import List, Optional, Sequence

try:
    from a2a.types import Artifact
except Exception as exc:
    raise ImportError("The 'a2a-sdk' package is required to use these selectors") from exc


def latest_artifacts(
        rows: Sequence[List[Artifact] | None],
        artifact_name: Optional[str] = None,
) -> List[Artifact]:
    """Return the latest version of each artifact, resolved by task creation order.

    Tasks are assumed to be pre-sorted by ``created_at`` descending.
    For each artifact name the first occurrence is taken as the latest version.
    """
    seen: set[str] = set()
    artifacts: List[Artifact] = []
    for task_artifacts in rows:
        for artifact in (task_artifacts or []):
            if artifact_name is not None and artifact.name != artifact_name:
                continue
            if artifact.name not in seen:
                seen.add(artifact.name)
                artifacts.append(artifact)
    return artifacts


def all_versions_by_name(
        rows: Sequence[List[Artifact] | None],
        artifact_name: str,
) -> List[Artifact]:
    """Return all versions of an artifact with the given name, sorted by task creation order (desc)."""
    artifacts: List[Artifact] = []
    for task_artifacts in rows:
        for artifact in (task_artifacts or []):
            if artifact.name == artifact_name:
                artifacts.append(artifact)
    return artifacts


def artifacts_by_version(
        rows: Sequence[List[Artifact] | None],
        artifact_version: str,
        artifact_name: Optional[str] = None,
) -> List[Artifact]:
    """Return all artifacts matching ``artifact_version`` across all tasks in scope."""
    artifacts: List[Artifact] = []
    for task_artifacts in rows:
        for artifact in (task_artifacts or []):
            if artifact_name is not None and artifact.name != artifact_name:
                continue
            if (artifact.metadata or {}).get("version") == artifact_version:
                artifacts.append(artifact)
    return artifacts
