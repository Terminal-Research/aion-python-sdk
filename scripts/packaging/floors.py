#!/usr/bin/env python3
"""Report the lower bound of every dependency range, and whether it was installed.

The manifest states ranges - ``a2a-sdk>=1.1.5,<1.2.0``, ``langgraph>=1.0.0``,
``google-adk>=1.27.1`` - and an ordinary install resolves each one to the
newest release that fits. So the top of every range is exercised on every run
and the bottom is a promise nothing checks: the SDK may already use a symbol
the floor release does not have, and the first to find out is whoever pins an
older version downstream.

``make tests-floors`` closes that by installing the project at the lowest
resolution its direct dependencies allow and running the unit suite there.
This script is the report that comes with it::

    scripts/packaging/floors.py                      print the declared floors
    scripts/packaging/floors.py --check              compare them with what is installed
    scripts/packaging/floors.py --test-requirements  what the unit suite needs installed

``--check`` compares every floor with what is installed. A floor that was not
installed exactly has one of two reasons, and only one is a defect:

* the floor is unreachable - no distribution for it exists for any supported
  interpreter, or another dependency contradicts it. This is a false
  statement in the manifest and is corrected there.
* a sibling requires more. google-adk 1.27.1 asks for ``fastapi>=0.124.1``
  while the ``server`` extra declares ``>=0.115.2``. Both are true for their
  own extras; in the combined install the resolver settles it.

Telling those apart takes reading once, so the second kind is written down in
``RAISED_BY_SIBLING`` with the sibling that raises it, and ``--check`` fails
on anything else: a floor lifted with no entry, an entry whose floor is now
installed exactly (it has gone stale), or a floor not installed at all.
The registry describes the default extras, the ones CI installs; for any
other selection ``--check`` reports and exits 0.

A package declared more than once - by a component extra and a composite,
or with a tighter bound in one extra than another - has as its floor the
highest of its lower bounds: that is the least any combined install takes.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

# The two extras a development install uses, and the two the CI matrix
# installs: the floors worth checking are the ones under test everywhere else.
DEFAULT_EXTRAS = ("langgraph-server", "adk-server")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The development group less what the unit suite never imports: the GraphQL
# code generator, the publishing tool and the layer linter. They only add
# packages the floors install would then have to resolve around.
NOT_FOR_THE_UNIT_SUITE = frozenset({"ariadne-codegen", "twine", "import-linter"})

# Floors a sibling dependency raises in the default combined install
# (langgraph-server + adk-server, Python 3.12), and the sibling that does.
# Checked both ways by --check: a lifted floor missing here fails the job,
# and so does an entry whose floor is installed exactly.
RAISED_BY_SIBLING: dict[str, str] = {
    "pydantic": "google-adk 1.27.1 requires pydantic>=2.12.0",
    "pydantic-settings": "mcp 1.23.0, under google-adk, requires pydantic-settings>=2.5.2",
    "pyjwt": "mcp 1.23.0, under google-adk, requires pyjwt>=2.10.1",
    "uvicorn": "google-adk 1.27.1 requires uvicorn>=0.34.0",
    "fastapi": "google-adk 1.27.1 requires fastapi>=0.124.1",
    # google-adk asks for opentelemetry-exporter-otlp-proto-http with no upper
    # bound; a transitive dependency resolves to its newest release, and each
    # exporter release pins the sdk and api of its own minor.
    "opentelemetry-api": (
        "opentelemetry-exporter-otlp-proto-http, under google-adk, pins the "
        "opentelemetry release of its own minor"
    ),
    "opentelemetry-sdk": (
        "opentelemetry-exporter-otlp-proto-http, under google-adk, pins the "
        "opentelemetry release of its own minor"
    ),
}


def _floor(requirement: Requirement) -> tuple[str, str] | None:
    """Return ``(name[extras], floor)`` for a requirement declaring one, else None."""
    for specifier in requirement.specifier:
        if specifier.operator in (">=", "~="):
            extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
            return f"{requirement.name}{extras}", specifier.version
    return None


def _declared(manifest: dict, extras: list[str]) -> list[tuple[str, str, Requirement]]:
    """Collect ``(name, floor, requirement)`` for the base plus the named extras.

    The composite extras are written out in full rather than as
    self-references, so a package appears more than once with the same bound.
    The first occurrence wins and the rest are dropped, which keeps the report
    one line per package.
    """
    project = manifest["project"]
    optional = project.get("optional-dependencies", {})

    declared: list[str] = list(project.get("dependencies", []))
    for extra in extras:
        if extra not in optional:
            raise SystemExit(
                f"No extra named {extra!r} in the manifest. "
                f"Available: {', '.join(sorted(optional))}"
            )
        declared.extend(optional[extra])

    highest: dict[str, tuple[str, str, Requirement]] = {}
    for raw in declared:
        requirement = Requirement(raw)
        found = _floor(requirement)
        if found is None:
            continue
        key = canonicalize_name(requirement.name)
        current = highest.get(key)
        if current is None or Version(found[1]) > Version(current[1]):
            highest[key] = (found[0], found[1], requirement)
    return list(highest.values())


def _report(floors: list[tuple[str, str, Requirement]], *, strict: bool) -> int:
    """Compare every floor with the installed version; with ``strict``, fail on the unexplained."""
    exact: list[str] = []
    explained: list[str] = []
    failures: list[str] = []
    for name, floor, requirement in floors:
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        package = name.split("[")[0]
        key = canonicalize_name(package)
        try:
            present = installed_version(package)
        except PackageNotFoundError:
            failures.append(f"  {package:<32} floor {floor:<12} not installed")
            continue
        if Version(present) == Version(floor):
            exact.append(package)
            if key in RAISED_BY_SIBLING:
                failures.append(
                    f"  {package:<32} floor {floor:<12} installed exactly, but "
                    f"RAISED_BY_SIBLING still says: {RAISED_BY_SIBLING[key]}"
                )
        elif key in RAISED_BY_SIBLING:
            explained.append(
                f"  {package:<32} floor {floor:<12} resolved to {present:<10} {RAISED_BY_SIBLING[key]}"
            )
        else:
            failures.append(f"  {package:<32} floor {floor:<12} resolved to {present}, unexplained")

    print(f"[floors] {len(exact)} of {len(floors)} floors installed exactly")
    if explained:
        print(f"[floors] {len(explained)} raised by a sibling, as RAISED_BY_SIBLING records:")
        print("\n".join(explained))
    if not failures:
        return 0
    print(f"[floors] {len(failures)} not accounted for:")
    print("\n".join(failures))
    print(
        "[floors] a floor no resolution can reach is a false bound: correct it in "
        "pyproject.toml. A floor a sibling raises goes into RAISED_BY_SIBLING with the "
        "sibling that raises it."
    )
    return 1 if strict else 0


def _test_requirements(manifest: dict) -> list[str]:
    """The development group's requirements the unit suite needs, as pip lines.

    Read from ``[tool.poetry.group.dev.dependencies]`` so the floors
    environment gets the same test tooling a development install has, without
    Poetry: the floors job builds an environment of its own.
    """
    group = manifest["tool"]["poetry"]["group"]["dev"]["dependencies"]
    lines: list[str] = []
    for name, spec in group.items():
        if canonicalize_name(name) in NOT_FOR_THE_UNIT_SUITE:
            continue
        if isinstance(spec, dict):
            extras = f"[{','.join(spec['extras'])}]" if spec.get("extras") else ""
            lines.append(f"{name}{extras}{spec.get('version', '')}")
        else:
            lines.append(f"{name}{spec}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the declared floors with the installed versions",
    )
    parser.add_argument(
        "--extras",
        default=",".join(DEFAULT_EXTRAS),
        help="Comma-separated extras to include (default: %(default)s)",
    )
    parser.add_argument(
        "--test-requirements",
        action="store_true",
        help="Print the development requirements the unit suite needs, one per line",
    )
    parser.add_argument(
        "--all-extras",
        action="store_true",
        help="Every extra the manifest declares, instead of --extras",
    )
    arguments = parser.parse_args(argv)

    manifest = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if arguments.all_extras:
        extras = sorted(manifest["project"].get("optional-dependencies", {}))
    else:
        extras = [name for name in arguments.extras.split(",") if name]

    if arguments.test_requirements:
        print("\n".join(_test_requirements(manifest)))
        return 0

    floors = _declared(manifest, extras)
    if arguments.check:
        return _report(floors, strict=sorted(extras) == sorted(DEFAULT_EXTRAS))

    print("# Generated by scripts/packaging/floors.py - do not edit.")
    print(f"# The bottom of every declared range for: {', '.join(extras) or 'base only'}")
    for name, floor, requirement in floors:
        line = f"{name}=={floor}"
        if requirement.marker is not None:
            line = f"{line} ; {requirement.marker}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
