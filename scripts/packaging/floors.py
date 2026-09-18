#!/usr/bin/env python3
"""Report the lower bound of every dependency range, and whether it was installed.

The manifest states ranges - ``a2a-sdk>=1.1.2,<1.2.0``, ``langgraph>=1.0.0``,
``google-adk>=1.20.0`` - and an ordinary install resolves each one to the
newest release that fits. So the top of every range is exercised on every run
and the bottom is a promise nothing checks: the SDK may already use a symbol
the floor release does not have, and the first to find out is whoever pins an
older version downstream.

``make tests-floors`` closes that by installing the project at the lowest
resolution its direct dependencies allow and running the unit suite there.
This script is the report that comes with it::

    scripts/packaging/floors.py            print the declared floors
    scripts/packaging/floors.py --check    compare them with what is installed

``--check`` prints one line per floor that was *not* installed. There are two
reasons for that and only one is a defect:

* the floor is unreachable - no distribution for it exists for any supported
  interpreter, or another dependency in the same extra contradicts it. This is
  a false statement in the manifest and should be corrected there.
* a sibling requires more. ``google-adk 1.20.0`` pins
  ``opentelemetry-api==1.37.0``, while the ``server`` extra inherits
  ``>=1.33.0`` from a2a-sdk. Both are true; the resolver settles it. Nothing
  to fix.

Telling those apart takes reading, so ``--check`` reports and exits 0. What
fails the job is the unit suite, which is the guarantee being made.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

# The two extras a development install uses, and the two the CI matrix
# installs: the floors worth checking are the ones under test everywhere else.
DEFAULT_EXTRAS = ("langgraph-server", "adk-server")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    seen: set[str] = set()
    floors: list[tuple[str, str, Requirement]] = []
    for raw in declared:
        requirement = Requirement(raw)
        found = _floor(requirement)
        if found is None or found[0] in seen:
            continue
        seen.add(found[0])
        floors.append((found[0], found[1], requirement))
    return floors


def _report(floors: list[tuple[str, str, Requirement]]) -> int:
    """Print every floor the installed environment sits above."""
    lifted: list[str] = []
    for name, floor, _ in floors:
        package = name.split("[")[0]
        try:
            present = installed_version(package)
        except PackageNotFoundError:
            lifted.append(f"  {package:<32} floor {floor:<12} not installed")
            continue
        if Version(present) > Version(floor):
            lifted.append(f"  {package:<32} floor {floor:<12} resolved to {present}")

    if not lifted:
        print("[floors] every declared floor is what is installed")
        return 0

    print(f"[floors] {len(lifted)} of {len(floors)} floors were not the version installed:")
    print("\n".join(lifted))
    print(
        "[floors] a floor no resolution can reach is a false bound and belongs in "
        "pyproject.toml; a floor a sibling raises is the resolver doing its job."
    )
    return 0


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

    floors = _declared(manifest, extras)
    if arguments.check:
        return _report(floors)

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
