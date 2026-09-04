#!/usr/bin/env python3
"""Check that the environment this interpreter runs in was installed cleanly.

Run under the environment being checked - ``poetry run ./scripts/packaging/
envcheck.py``, or ``make check-env`` - right after installing it. Two things
are asked, and both of them have been wrong here before:

*One version per package.* An installer that unpacks two versions of the same
distribution into one site-packages leaves two ``*.dist-info`` directories
behind and files from either version written over each other. Nothing else
notices: imports keep working, they just import the wrong halves. This is what
``installer.re-resolve`` in poetry.toml is set for, and this check is what says
whether that setting is still doing its job.

*A resolvable set.* ``pip check`` reads the installed metadata and reports
requirements that are missing or contradicted. A non-zero exit is a failure
here, whatever it says.

Exit status is 0 when both passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import sysconfig
from collections import defaultdict
from pathlib import Path

SUFFIX = ".dist-info"


def canonical(name: str) -> str:
    """PEP 503 normalisation, so ``ruamel_yaml`` and ``ruamel.yaml`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def default_site_packages() -> list[Path]:
    """The directories this interpreter installs distributions into."""
    paths = []
    for key in ("purelib", "platlib"):
        path = Path(sysconfig.get_paths()[key])
        if path not in paths:
            paths.append(path)
    return paths


def installed_versions(site_packages: list[Path]) -> dict[str, list[tuple[str, str]]]:
    """Map canonical project name -> [(version, directory name), ...]."""
    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for directory in site_packages:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob("*" + SUFFIX)):
            name, _, version = entry.name[: -len(SUFFIX)].rpartition("-")
            if not name:
                # Not "<name>-<version>.dist-info"; leave it to the installer.
                continue
            found[canonical(name)].append((version, entry.name))
    return dict(found)


def duplicates(site_packages: list[Path]) -> dict[str, list[tuple[str, str]]]:
    """The packages installed more than once, by canonical name."""
    return {
        name: entries
        for name, entries in installed_versions(site_packages).items()
        if len(entries) > 1
    }


def check_one_version_each(site_packages: list[Path]) -> list[str]:
    """Report lines; failures are the lines starting with FAIL."""
    lines = [
        f"        {directory}" + ("" if directory.is_dir() else "  (does not exist)")
        for directory in site_packages
    ]
    found = duplicates(site_packages)
    if found:
        for name, entries in sorted(found.items()):
            versions = ", ".join(version for version, _ in sorted(entries))
            lines.append(f"  FAIL  {name} is installed {len(entries)} times: {versions}")
    else:
        total = len(installed_versions(site_packages))
        lines.append(f"  ok    {total} distributions, one version each")
    return lines


def check_pip(python: str) -> list[str]:
    result = subprocess.run(
        [python, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return ["  ok    pip check found no broken requirements"]
    output = (result.stdout + result.stderr).strip().splitlines()
    return [f"  FAIL  pip check exited {result.returncode}"] + [
        f"          {line}" for line in output
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site-packages",
        type=Path,
        action="append",
        dest="site_packages",
        help="directory to inspect instead of this interpreter's "
        "(repeatable; skips the pip check, which has no meaning elsewhere)",
    )
    args = parser.parse_args()

    lines = ["\none version per package"]
    if args.site_packages:
        lines += check_one_version_each(args.site_packages)
        lines.append("\npip check")
        lines.append("  skip  --site-packages given: not this interpreter's environment")
    else:
        lines += check_one_version_each(default_site_packages())
        lines.append("\npip check")
        lines += check_pip(sys.executable)

    print(f"checking {sys.executable}" if not args.site_packages else "checking given directories")
    print("\n".join(lines))

    failures = [line for line in lines if line.lstrip().startswith("FAIL")]
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
