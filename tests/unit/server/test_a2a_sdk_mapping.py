"""``docs/development/a2a-sdk-mapping.md`` agrees with the code it maps.

The page is where a person or an agent looks up what Aion does with an a2a-sdk
entry point. Its table of overridden methods is checked against
``OVERRIDES`` in ``test_sdk_override_parity.py`` in both directions, and
``OVERRIDES`` against the a2a-sdk methods the Aion classes actually redefine,
so no override can be added or dropped without the page. Every test file the
page names has to exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.unit.server.test_sdk_override_parity import OVERRIDES

REPOSITORY = Path(__file__).resolve().parents[3]
MAPPING = REPOSITORY / "docs" / "development" / "a2a-sdk-mapping.md"
TABLE_HEADING = "## Overridden methods"
STATUSES = {"as is", "extended", "replaced", "not used"}


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    following = text.find("\n## ", start)
    return text[start:] if following == -1 else text[start:following]


def _override_rows() -> list[list[str]]:
    rows = []
    for line in _section(MAPPING.read_text(), TABLE_HEADING).splitlines():
        if not line.startswith("| `"):
            continue
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _name(cell: str) -> str:
    match = re.fullmatch(r"`([A-Za-z0-9_]+\.[A-Za-z0-9_]+)`", cell)
    assert match, f"not a single `Class.method` cell: {cell!r}"
    return match.group(1)


def test_the_page_lists_exactly_the_overrides_the_code_has() -> None:
    documented = {(_name(row[0]), _name(row[1])) for row in _override_rows()}
    overridden = {
        (f"{override.__name__}.{method}", f"{base.__name__}.{method}")
        for override, base, method in OVERRIDES
    }

    assert documented - overridden == set(), "documented overrides the code does not have"
    assert overridden - documented == set(), "overrides missing from the mapping page"


def test_every_override_row_has_a_status_a_reason_and_a_test() -> None:
    for row in _override_rows():
        assert len(row) == 5, f"a row with {len(row)} columns: {row}"
        name, _base, status, reason, tested = row
        assert status in STATUSES, f"{name}: unknown status {status!r}"
        assert reason, f"{name}: no reason given"
        assert re.search(r"`[^`]*test_[^`]+\.py`", tested), f"{name}: no test named"


def test_every_test_file_the_page_names_exists() -> None:
    named = set(re.findall(r"`((?:[a-z_]+/)?test_[a-z0-9_]+\.py)`", MAPPING.read_text()))
    tests = REPOSITORY / "tests"

    missing = sorted(name for name in named if not any(tests.rglob(name)))
    assert not missing, f"the mapping page names test files that do not exist: {missing}"


def test_every_a2a_sdk_method_an_aion_class_redefines_is_listed() -> None:
    """Found by introspection, so a new override cannot stay out of the table."""
    import inspect

    classes = {override for override, _, _ in OVERRIDES}
    listed = {(override, method) for override, _, method in OVERRIDES}
    found = set()
    for cls in classes:
        bases = [base for base in cls.__mro__[1:] if base.__module__.startswith("a2a.")]
        for name, value in vars(cls).items():
            if name.startswith("__") or not inspect.isfunction(value):
                continue
            if any(name in vars(base) for base in bases):
                found.add((cls, name))

    unlisted = sorted(f"{cls.__name__}.{name}" for cls, name in found - listed)
    assert not unlisted, f"a2a-sdk methods overridden but not in OVERRIDES: {unlisted}"
