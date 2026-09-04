#!/usr/bin/env python3
"""Check the built wheel and sdist against the packaging contract.

Run after ``poetry build``; it reads ``dist/`` and the root manifest and
nothing else, so what it inspects is exactly what would be uploaded.

Two of its jobs are worth naming, because nothing else in the repository does
them:

*Layout.* One PyPI project ships every ``aion.*`` subpackage, and the three
namespace levels (``aion``, ``aion.langgraph``, ``aion.adk``) must stay free of
``__init__.py`` or a second distribution sharing the namespace - the behaviour
evolution toolkit does - stops being importable.

*Extras drift.* The composite extras are written out in full instead of
referring to their parts (see the comment in pyproject.toml: Poetry's solver
cannot install a self-referential extra), so ``langgraph-server`` no longer
gets its copy of ``server`` from the packaging tooling. The set relations those
unions are supposed to satisfy are asserted here instead, and this is the only
place they are.

Exit status is 0 when every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import Parser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECT_NAME = "aionto-sdk"

# The public import paths. Every one of them is in every wheel: extras install
# third-party libraries, never Aion code.
CONTRACT_MODULES = (
    "aion.core",
    "aion.api",
    "aion.db",
    "aion.mcp",
    "aion.server",
    "aion.proxy",
    "aion.cli",
    "aion.langgraph.authoring",
    "aion.langgraph.server",
    "aion.adk.authoring",
    "aion.adk.server",
)

# Directories that are namespace portions, not packages.
NAMESPACE_LEVELS = ("aion", "aion/langgraph", "aion/adk")

# Shipped inside the wheel rather than fetched at run time.
DATA_FILES = ("aion/cli/bin/cli.mjs",)

EXPECTED_EXTRAS = frozenset(
    {
        "server",
        "langgraph-authoring",
        "langgraph-server",
        "adk-authoring",
        "adk-server",
    }
)

# left ⊇ union(right): a dependency added to a component extra has to reach the
# composites that advertise it.
EXTRA_UNIONS = (
    ("langgraph-server", ("langgraph-authoring", "server")),
    ("adk-server", ("adk-authoring", "server")),
)

# "a2a-sdk[http-server,telemetry,encryption]", written out - see pyproject.toml.
# Checked against a2a-sdk's own metadata when it is installed, so the copy
# cannot quietly fall behind the original.
A2A_EXTRAS = ("http-server", "telemetry", "encryption")
A2A_EXPANSION = frozenset(
    {"starlette", "sse-starlette", "opentelemetry-api", "opentelemetry-sdk", "cryptography"}
)

# Name (spec) ; marker  - the shape poetry-core writes into METADATA.
REQUIREMENT = re.compile(
    r"""^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*
        (?:\[(?P<extras>[^]]*)\])?\s*
        (?:\((?P<paren>[^)]*)\)|(?P<bare>[^;]*?))?\s*
        (?:;\s*(?P<marker>.*))?$""",
    re.VERBOSE,
)
EXTRA_MARKER = re.compile(r"""extra\s*==\s*["'](?P<extra>[^"']+)["']""")


def canonical(name: str) -> str:
    """PEP 503 normalisation, so ``PyYAML`` and ``pyyaml`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


class Report:
    """Collected outcomes, printed once at the end."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def ok(self, message: str) -> None:
        self.lines.append(f"  ok    {message}")

    def skip(self, message: str) -> None:
        self.lines.append(f"  skip  {message}")

    def fail(self, message: str) -> None:
        self.lines.append(f"  FAIL  {message}")
        self.failures.append(message)

    def check(self, condition: bool, ok_message: str, fail_message: str) -> bool:
        if condition:
            self.ok(ok_message)
        else:
            self.fail(fail_message)
        return condition

    def section(self, title: str) -> None:
        self.lines.append(f"\n{title}")


def find_artifacts(dist_dir: Path, report: Report) -> tuple[Path | None, Path | None]:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    report.section("distribution files")
    report.check(
        len(wheels) == 1,
        f"exactly one wheel: {wheels[0].name}" if len(wheels) == 1 else "",
        f"expected exactly one wheel in {dist_dir}, found {len(wheels)}: "
        f"{[p.name for p in wheels]}",
    )
    report.check(
        len(sdists) == 1,
        f"exactly one sdist: {sdists[0].name}" if len(sdists) == 1 else "",
        f"expected exactly one sdist in {dist_dir}, found {len(sdists)}: "
        f"{[p.name for p in sdists]}",
    )
    other = sorted(
        p.name for p in dist_dir.iterdir() if p.suffix not in {".whl", ".gz"} and p.is_file()
    )
    report.check(
        not other,
        "nothing else in the directory",
        f"unexpected files in {dist_dir}: {other}",
    )
    return (wheels[0] if len(wheels) == 1 else None, sdists[0] if len(sdists) == 1 else None)


def check_wheel_contents(wheel: Path, report: Report) -> list[str]:
    report.section(f"wheel contents ({wheel.name})")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    for module in CONTRACT_MODULES:
        prefix = module.replace(".", "/") + "/"
        report.check(
            any(n.startswith(prefix) and n.endswith(".py") for n in names),
            f"{module} is in the wheel",
            f"{module} is missing from the wheel",
        )

    for level in NAMESPACE_LEVELS:
        marker = f"{level}/__init__.py"
        report.check(
            marker not in names,
            f"{level} stays a namespace portion",
            f"{marker} is in the wheel: it would shadow other distributions "
            f"sharing the aion namespace",
        )

    for data_file in DATA_FILES:
        report.check(
            data_file in names,
            f"{data_file} is in the wheel",
            f"{data_file} is missing from the wheel",
        )

    return names


def check_sdist_matches(sdist: Path, wheel_names: Iterable[str], report: Report) -> None:
    report.section(f"sdist contents ({sdist.name})")
    with tarfile.open(sdist) as archive:
        sdist_names = archive.getnames()

    wheel_py = sum(1 for n in wheel_names if n.endswith(".py"))
    sdist_py = sum(1 for n in sdist_names if n.endswith(".py"))
    report.check(
        wheel_py == sdist_py,
        f"the same {wheel_py} Python files as the wheel",
        f"the sdist has {sdist_py} Python files, the wheel has {wheel_py}: "
        f"a wheel built from this sdist would not match the one shipped beside it",
    )


def parse_requirement(raw: str) -> tuple[str, frozenset[str], str, str | None]:
    """Split one Requires-Dist line into name, extras, specifier and marker."""
    match = REQUIREMENT.match(raw.strip())
    if match is None:  # pragma: no cover - METADATA is machine written
        raise ValueError(f"cannot parse Requires-Dist: {raw!r}")
    specifier = (match.group("paren") or match.group("bare") or "").strip()
    extras = frozenset(
        canonical(part) for part in (match.group("extras") or "").split(",") if part.strip()
    )
    return canonical(match.group("name")), extras, specifier, match.group("marker")


def requirements_by_extra(
    metadata_text: str,
) -> tuple[dict[str, set[tuple[str, str]]], list[str]]:
    """Group requirements by the extra whose marker selects them."""
    message = Parser().parsestr(metadata_text)
    grouped: dict[str, set[tuple[str, str]]] = {"": set()}
    raw_lines: list[str] = []
    for raw in message.get_all("Requires-Dist") or []:
        raw_lines.append(raw)
        name, _extras, specifier, marker = parse_requirement(raw)
        found = EXTRA_MARKER.findall(marker or "")
        for extra in found or [""]:
            grouped.setdefault(extra, set()).add((name, specifier))
    return grouped, raw_lines


def read_metadata(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        return archive.read(name).decode("utf-8")


def check_metadata(wheel: Path, report: Report) -> None:
    report.section("wheel metadata")
    metadata_text = read_metadata(wheel)
    message = Parser().parsestr(metadata_text)

    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]
    report.check(
        canonical(message["Name"] or "") == canonical(project["name"]),
        f"name matches the manifest: {message['Name']}",
        f"wheel name {message['Name']!r} does not match "
        f"[project].name {project['name']!r}",
    )
    report.check(
        (message["Version"] or "") == project["version"],
        f"version matches the manifest: {message['Version']}",
        f"wheel version {message['Version']!r} does not match "
        f"[project].version {project['version']!r}",
    )

    provided = {canonical(e) for e in (message.get_all("Provides-Extra") or [])}
    expected = {canonical(e) for e in EXPECTED_EXTRAS}
    report.check(
        provided == expected,
        f"extras: {', '.join(sorted(provided))}",
        f"extras are {sorted(provided)}, expected {sorted(expected)}",
    )

    grouped, raw_lines = requirements_by_extra(metadata_text)

    # A direct reference - "name @ git+https://..." or a local path - cannot be
    # uploaded to PyPI and would pin consumers to a checkout.
    direct = [line for line in raw_lines if "@" in line.split(";")[0]]
    report.check(
        not direct,
        "no direct (git or path) references",
        f"direct references in METADATA: {direct}",
    )

    # The ten libs are gone. Anything aionto-* left over is either a stale
    # per-lib dependency or a self-reference to this same project.
    foreign = sorted(
        {
            name
            for group in grouped.values()
            for name, _spec in group
            if name.startswith("aionto") and name != canonical(PROJECT_NAME)
        }
    )
    report.check(
        not foreign,
        "no dependencies on other aionto-* projects",
        f"METADATA depends on other aionto-* projects: {foreign}",
    )

    for composite, parts in EXTRA_UNIONS:
        union: set[tuple[str, str]] = set()
        for part in parts:
            union |= grouped.get(canonical(part), set())
        missing = sorted(union - grouped.get(canonical(composite), set()))
        report.check(
            not missing,
            f"[{composite}] covers {' + '.join(f'[{p}]' for p in parts)}",
            f"[{composite}] is missing {missing}, which "
            f"{' + '.join(f'[{p}]' for p in parts)} require - the composite extras are "
            f"written out by hand and this one has drifted",
        )

    check_a2a_expansion(grouped, report)


def check_a2a_expansion(grouped: dict[str, set[tuple[str, str]]], report: Report) -> None:
    """Compare the written-out a2a-sdk extras with a2a-sdk's own metadata."""
    try:
        from importlib.metadata import metadata as installed_metadata

        a2a = installed_metadata("a2a-sdk")
    except Exception:
        report.skip("a2a-sdk is not installed: cannot verify its written-out extras")
        return

    wanted = {canonical(extra) for extra in A2A_EXTRAS}
    expected: set[str] = set()
    for raw in a2a.get_all("Requires-Dist") or []:
        name, _extras, _spec, marker = parse_requirement(raw)
        if wanted & {canonical(e) for e in EXTRA_MARKER.findall(marker or "")}:
            expected.add(name)

    if expected != A2A_EXPANSION:
        report.fail(
            f"a2a-sdk {a2a['Version']} extras {sorted(A2A_EXTRAS)} now mean "
            f"{sorted(expected)}, but pyproject.toml writes them out as "
            f"{sorted(A2A_EXPANSION)}"
        )
        return

    server = grouped.get("server", set())
    missing = sorted(A2A_EXPANSION - {name for name, _spec in server})
    report.check(
        not missing,
        f"[server] carries a2a-sdk{list(A2A_EXTRAS)} written out",
        f"[server] is missing {missing} from the written-out a2a-sdk extras",
    )


def check_twine(dist_dir: Path, report: Report) -> None:
    report.section("twine")
    result = subprocess.run(
        [sys.executable, "-m", "twine", "check", *sorted(str(p) for p in dist_dir.iterdir())],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        report.ok("twine check passed")
    else:
        report.fail(
            "twine check failed:\n"
            + "\n".join(f"    {line}" for line in (result.stdout + result.stderr).splitlines())
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory holding the built distributions (default: ./dist)",
    )
    args = parser.parse_args()

    dist_dir = args.dist
    if not dist_dir.is_dir():
        print(f"{dist_dir} does not exist: run `make dist-build` first", file=sys.stderr)
        return 1

    report = Report()
    wheel, sdist = find_artifacts(dist_dir, report)
    if wheel is not None:
        names = check_wheel_contents(wheel, report)
        if sdist is not None:
            check_sdist_matches(sdist, names, report)
        check_metadata(wheel, report)
        check_twine(dist_dir, report)

    print(f"checking {dist_dir}")
    print("\n".join(report.lines))
    if report.failures:
        print(f"\n{len(report.failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
