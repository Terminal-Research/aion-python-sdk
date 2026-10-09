"""Every ``TODO`` in the SDK's Python code has its section in ``open-items.md``.

A ``TODO`` names a slug, and the reason it is open lives in
``docs/development/open-items.md`` under that slug, so the comment stays one
line and the registry is the one list of what is knowingly unfinished. A
``TODO`` without a slug would sit outside that list, so it fails here too.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = REPOSITORY / "src" / "aion"
REGISTRY = REPOSITORY / "docs" / "development" / "open-items.md"
STATUSES = {"Not supported yet", "Planned", "Needs verification"}

TODO = re.compile(r"\bTODO\b(?:\(([a-z0-9-]+)\))?")


def _sections() -> dict[str, str]:
    parts = re.split(r"^## ", REGISTRY.read_text(), flags=re.MULTILINE)[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


def _todos() -> list[tuple[str, int, str | None]]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            for match in TODO.finditer(line):
                found.append((str(path.relative_to(REPOSITORY)), number, match.group(1)))
    return found


def test_every_todo_names_a_slug_the_registry_has() -> None:
    slugs = set(_sections())
    problems = [
        f"{path}:{number}: " + ("TODO without a slug" if slug is None else f"no section '## {slug}'")
        for path, number, slug in _todos()
        if slug not in slugs
    ]
    assert not problems, "\n".join(problems)


def test_every_section_has_a_status_and_existing_code() -> None:
    for slug, section in _sections().items():
        status = re.search(r"^- \*\*Status:\*\* (.+)$", section, flags=re.MULTILINE)
        code = re.search(r"^- \*\*Code:\*\* (.+)$", section, flags=re.MULTILINE)
        assert status and status.group(1).strip() in STATUSES, f"{slug}: status must be one of {STATUSES}"
        assert code, f"{slug}: no Code line"
        for path in re.findall(r"`([^`]+)`", code.group(1)):
            assert (REPOSITORY / path).exists(), f"{slug}: {path} does not exist"
