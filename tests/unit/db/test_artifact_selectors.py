"""Selecting artifact versions out of the rows the tasks repository read."""

from __future__ import annotations

from a2a.types import Artifact, Part

from aion.db.postgres.repositories.tasks.selectors import artifacts_by_version


def _artifact(name: str, version: str | None) -> Artifact:
    artifact = Artifact(artifact_id=f"{name}-{version}", name=name, parts=[Part(text=name)])
    if version is not None:
        artifact.metadata["version"] = version
    return artifact


def test_versions_are_read_from_the_struct_metadata() -> None:
    """``metadata`` is a protobuf Struct, not a dict: it has no ``get``.

    Reading it as a dict raised on every artifact, and the artifact
    service's DB fallback turned that into "no artifacts".
    """
    rows = [
        [_artifact("report.txt", "1"), _artifact("notes.txt", "1")],
        [_artifact("report.txt", "0"), _artifact("unversioned.txt", None)],
    ]

    assert [a.artifact_id for a in artifacts_by_version(rows, "1")] == ["report.txt-1", "notes.txt-1"]
    assert [a.artifact_id for a in artifacts_by_version(rows, "0", "report.txt")] == ["report.txt-0"]
    assert artifacts_by_version(rows, "7") == []
