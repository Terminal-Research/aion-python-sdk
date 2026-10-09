"""The "Database migrations" registry of ``a2a-sdk-mapping.md`` matches a2a-sdk.

The SDK never runs a2a-sdk's own migrations, so a change a2a-sdk makes to the
tables Aion keeps on its models reaches a database only through an SDK
revision. The registry is where each a2a-sdk revision is either matched by one
or marked ``not applied``. This test reads the revisions of the installed
a2a-sdk, so an upgrade that brings a new one fails here until the registry,
and the migration it asks for, are written.
"""

from __future__ import annotations

from pathlib import Path

import a2a
from alembic.script import ScriptDirectory

from aion.db.postgres import migrations

REPOSITORY = Path(__file__).resolve().parents[3]
MAPPING = REPOSITORY / "docs" / "development" / "a2a-sdk-mapping.md"
HEADING = "## Database migrations"
NOT_APPLIED = "not applied"


def _rows() -> list[list[str]]:
    text = MAPPING.read_text()
    start = text.index(HEADING) + len(HEADING)
    following = text.find("\n## ", start)
    section = text[start:] if following == -1 else text[start:following]
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("| `")
    ]


def _revisions(directory: Path) -> set[str]:
    return {script.revision for script in ScriptDirectory(str(directory)).walk_revisions()}


def _code(cell: str) -> str:
    assert cell.startswith("`") and cell.endswith("`"), f"not a `revision` cell: {cell!r}"
    return cell.strip("`")


def test_the_registry_lists_exactly_the_revisions_a2a_sdk_has() -> None:
    a2a_sdk = _revisions(Path(a2a.__file__).parent / "migrations")
    listed = {_code(row[0]) for row in _rows()}

    assert a2a_sdk - listed == set(), "a2a-sdk revisions missing from the registry"
    assert listed - a2a_sdk == set(), "registry rows for revisions a2a-sdk does not have"


def test_every_row_names_an_sdk_revision_or_why_not() -> None:
    sdk = _revisions(Path(migrations.__file__).parent)

    for row in _rows():
        assert len(row) == 4, f"a row with {len(row)} columns: {row}"
        revision, change, applied_by, why = row
        assert change and why, f"{revision}: the change and the reason are both required"
        if applied_by != NOT_APPLIED:
            assert _code(applied_by) in sdk, f"{revision}: no SDK revision {applied_by}"
