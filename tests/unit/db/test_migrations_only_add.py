"""New revisions only add: the previous release keeps working on the new schema.

Servers of an agent roll out one at a time, and a release can be rolled back
onto a database a newer one migrated (``AGENTS.md``). So a revision's
``upgrade()`` may add tables, columns and indexes, but may not drop or rename
a table or a column, retype one, or make one NOT NULL. Removing takes two
releases: the code stops using the column or table in one, and a later
revision drops it. Such a revision is listed in ``TWO_RELEASE_REMOVALS`` with
what it removes and the release that stopped using it, so the exception is
written down where a reviewer sees it.

Revisions up to ``LAST_REVISION_BEFORE_THE_RULE`` were written before the rule
and are not checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from aion.db.postgres import migrations

LAST_REVISION_BEFORE_THE_RULE = "009"

TWO_RELEASE_REMOVALS: dict[str, str] = {}
"""Revision id -> what it removes, and the release whose code stopped using it."""

REMOVING_CALLS = {"drop_table", "drop_column", "rename_table"}
REMOVING_SQL = ("DROP TABLE", "DROP COLUMN", "RENAME ")


def _removals(source: str) -> list[str]:
    """What ``upgrade()`` in a revision's source removes or changes in place."""
    found = []
    for function in ast.walk(ast.parse(source)):
        if not (isinstance(function, ast.FunctionDef) and function.name == "upgrade"):
            continue
        for call in ast.walk(function):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            name = call.func.attr
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if name in REMOVING_CALLS:
                found.append(f"op.{name}")
            elif name == "alter_column":
                if "new_column_name" in keywords or "type_" in keywords:
                    found.append("op.alter_column renames or retypes a column")
                nullable = keywords.get("nullable")
                if isinstance(nullable, ast.Constant) and nullable.value is False:
                    found.append("op.alter_column makes a column NOT NULL")
            elif name == "execute":
                text = ast.unparse(call).upper()
                found.extend(f"op.execute with {sql.strip()}" for sql in REMOVING_SQL if sql in text)
    return found


def _revisions_under_the_rule() -> list:
    script = ScriptDirectory(str(Path(migrations.__file__).parent))
    before = {revision.revision for revision in script.iterate_revisions(LAST_REVISION_BEFORE_THE_RULE, "base")}
    return [revision for revision in script.walk_revisions() if revision.revision not in before]


def test_revisions_after_the_rule_only_add() -> None:
    problems = []
    for revision in _revisions_under_the_rule():
        if revision.revision in TWO_RELEASE_REMOVALS:
            continue
        found = _removals(Path(revision.path).read_text())
        if found:
            problems.append(f"{revision.revision}: {', '.join(found)}")
    assert not problems, (
        "a revision removes what the previous release may still use; remove it in two releases "
        "and list the second revision in TWO_RELEASE_REMOVALS:\n" + "\n".join(problems)
    )


def test_every_listed_removal_names_a_revision_and_why() -> None:
    known = {revision.revision for revision in _revisions_under_the_rule()}
    for revision, reason in TWO_RELEASE_REMOVALS.items():
        assert revision in known, f"{revision} is not a revision after {LAST_REVISION_BEFORE_THE_RULE}"
        assert reason.strip(), f"{revision}: say what it removes and which release stopped using it"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("op.drop_column('tasks', 'old')", "op.drop_column"),
        ("op.drop_table('old')", "op.drop_table"),
        ("op.rename_table('old', 'new')", "op.rename_table"),
        ("op.alter_column('tasks', 'old', new_column_name='new')", "renames or retypes"),
        ("op.alter_column('tasks', 'note', nullable=False)", "NOT NULL"),
        ("op.execute('ALTER TABLE tasks DROP COLUMN old')", "DROP COLUMN"),
    ],
)
def test_the_check_recognises_a_removal(body: str, expected: str) -> None:
    found = _removals(f"def upgrade():\n    {body}\n")

    assert any(expected in item for item in found), found


def test_the_check_finds_the_removal_a_revision_before_the_rule_made() -> None:
    """008 dropped the claims table in one release, which is what the rule now forbids."""
    script = ScriptDirectory(str(Path(migrations.__file__).parent))

    assert "op.drop_table" in _removals(Path(script.get_revision("008").path).read_text())


def test_the_check_lets_additions_through() -> None:
    source = (
        "def upgrade():\n"
        "    op.add_column('tasks', sa.Column('note', sa.Text(), nullable=True))\n"
        "    op.create_index('ix_tasks_note', 'tasks', ['note'])\n"
        "def downgrade():\n"
        "    op.drop_column('tasks', 'note')\n"
    )

    assert _removals(source) == []
