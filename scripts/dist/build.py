#!/usr/bin/env python3
"""
Build the publishable `aionto-*` distributions.

Each distribution is assembled in a temporary directory outside the working
tree and built there. The detour is not incidental: poetry-core asks git which
files are ignored and drops those from the archives, so a `src/` tree generated
inside the repository and git-ignored builds into an empty wheel that looks
perfectly healthy. A staging directory has no `.git` above it, so the rule
never fires and what was copied is what ships.

What is copied: the distribution's committed `pyproject.toml`, a README, the
repository `LICENSE`, and the `src/aion` tree of every library the
distribution bundles, minus caches. The README is the one named by
`[tool.aion-dist] readme`, which defaults to the README of the single library it bundles;
its relative links are rewritten to absolute GitHub URLs against the directory
it came from. Sources are checked on the way in and the
built archives are checked on the way out — collisions between bundled trees,
an `__init__.py` at a namespace level, and files that were staged but did not
reach the wheel all fail the build rather than reaching PyPI.

The staged `pyproject.toml` is completed before Poetry sees it. A distribution
declares neither its own version nor a version on its `aionto-*` siblings —
both are the version of `libs/aion-sdk`, and are written here, into the copy.
Nothing under `distributions/` holds a version number, so preparing a release
means bumping that one library and nothing else.

Usage:
    python build.py --all                 # build every distribution into dist/
    python build.py aionto-sdk            # build named distributions only

Example:
    $ python scripts/dist/build.py --all
    [INFO] Building 5 distributions at version 0.1.0
    [DIST] aionto-sdk 0.1.0: staging aion-core, aion-api-client, aion-db, aion-mcp, aion-server, aion-sdk
      [OK] 412 files staged
      [OK] aionto_sdk-0.1.0-py3-none-any.whl
      [OK] aionto_sdk-0.1.0.tar.gz
    ...
    [COMPLETE] 10 artifacts in dist/
"""

import argparse
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Sequence

from config import (
    DIST_DIR,
    DIST_PREFIX,
    Distribution,
    DistributionError,
    LICENSE_FILE,
    NAMESPACE_DIRS,
    ROOT_DIR,
    check_version_lockstep,
    find_distributions,
    parse_requirement,
)

# Copied trees are working directories, not clean checkouts. These entries are
# build and test residue that must not travel into an archive.
EXCLUDED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

GITHUB_BLOB_BASE = "https://github.com/Terminal-Research/aion-python-sdk/blob/main/"


def resolve_relative_links(text: str, base: str) -> str:
    """
    Rewrite relative Markdown links as absolute GitHub URLs for PyPI.

    PyPI serves the long description from its own domain, where a relative link
    resolves against pypi.org and 404s. Links resolve against `base`, the
    README's own directory, not against the repository root: a README shipped
    out of `libs/<lib>/` links relative to itself, so
    `../aion-authoring-langgraph/README.md` is a sibling library rather than a
    path from the root.

    Args:
        text: Markdown source
        base: Directory holding the README, relative to the repository root and
            `""` for the repository README itself

    Returns:
        The Markdown with every relative link made absolute
    """
    def _absolutize(match: re.Match[str]) -> str:
        url = match.group(1)
        if url.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        path, _, anchor = url.partition("#")
        resolved = posixpath.normpath(posixpath.join(base, path))
        return f"]({GITHUB_BLOB_BASE}{resolved}{'#' + anchor if anchor else ''})"

    return re.sub(r"\]\(([^)]+)\)", _absolutize, text)


def stage_pyproject(distribution: Distribution, staging: Path) -> None:
    """
    Copy the distribution's `pyproject.toml` and write the derived version in.

    The committed file declares `version` dynamic and names its `aionto-*`
    siblings without a specifier, because the release version is a property of
    the bundled libraries rather than of this file. Two things are therefore
    added to the copy: `[tool.poetry] version`, which is where poetry-core
    looks when `[project]` declares the version dynamic, and an `==` pin on
    every sibling, so an installed set cannot mix versions.

    Args:
        distribution: The distribution being assembled
        staging: Directory the distribution is built in

    Raises:
        DistributionError: If the file has no `[tool.poetry]` table to write
            the version into, or a sibling requirement was not pinned
    """
    version = distribution.version
    text = distribution.pyproject.read_text(encoding="utf-8")

    text, table_count = re.subn(
        r"^\[tool\.poetry\]$",
        f'[tool.poetry]\nversion = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    if table_count != 1:
        raise DistributionError(
            f"{distribution.pyproject}: expected exactly one [tool.poetry] table to "
            f"carry the version, found {table_count}"
        )

    # Every `aionto-*` in this file is a requirement except the distribution's
    # own `name = "..."`, which the lookbehind leaves alone.
    text, pin_count = re.subn(
        rf'(?<!name = )"({re.escape(DIST_PREFIX)}[a-z0-9-]+)"',
        rf'"\g<1>=={version}"',
        text,
    )
    expected = sum(
        1
        for requirement in distribution.requirements
        if parse_requirement(requirement)[0].startswith(DIST_PREFIX)
    )
    if pin_count != expected:
        raise DistributionError(
            f"{distribution.pyproject}: pinned {pin_count} sibling requirements, "
            f"but the file declares {expected}"
        )

    (staging / "pyproject.toml").write_text(text, encoding="utf-8")


def stage_sources(distribution: Distribution, staging: Path) -> Dict[Path, Path]:
    """
    Copy every bundled library's sources into one `src/` tree.

    The bundled libraries all publish into the same `aion` namespace, so their
    trees are merged rather than nested. Two libraries owning the same module
    path is a packaging fault that installation order would otherwise decide
    silently, so a destination that already exists fails the build.

    Args:
        distribution: The distribution being assembled
        staging: Directory the distribution is built in

    Returns:
        Mapping of path relative to `src/` to the source file it came from

    Raises:
        DistributionError: If two bundled libraries stage the same path
    """
    staged: Dict[Path, Path] = {}
    origins: Dict[Path, str] = {}

    for bundle, source_dir in zip(distribution.bundles, distribution.source_dirs):
        # Only the `aion` tree is a distribution's content. A library's `src/`
        # may hold other files - `aion-server` carries an empty `src/__init__.py`
        # - and poetry-core would not ship them anyway.
        for source in sorted((source_dir / "aion").rglob("*")):
            if any(part in EXCLUDED_DIR_NAMES for part in source.parts):
                continue
            if not source.is_file() or source.suffix in EXCLUDED_SUFFIXES:
                continue

            relative = source.relative_to(source_dir)
            if relative in staged:
                raise DistributionError(
                    f"{distribution.name}: {relative} is provided by both "
                    f"{origins[relative]} and {bundle}"
                )

            target = staging / "src" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged[relative] = source
            origins[relative] = bundle

    if not staged:
        raise DistributionError(f"{distribution.name}: bundled libraries staged no files")

    return staged


def check_namespace_dirs(distribution: Distribution, paths: Sequence[str], where: str) -> None:
    """
    Assert that the shared namespace levels carry no `__init__.py`.

    `aion`, `aion.langgraph` and `aion.adk` are PEP 420 implicit namespace
    packages split across distributions. A regular `__init__.py` at any of
    those levels makes one distribution's copy of the level authoritative and
    hides every sibling's subpackage from imports.

    Args:
        distribution: The distribution being built
        paths: Paths to inspect, `/`-separated and relative to `src/`
        where: What is being inspected, named in the error message

    Raises:
        DistributionError: If a namespace level carries an `__init__.py`
    """
    forbidden = {f"{namespace}/__init__.py" for namespace in NAMESPACE_DIRS}
    found = sorted(forbidden.intersection(paths))
    if found:
        raise DistributionError(
            f"{distribution.name}: {where} contains namespace __init__.py: {', '.join(found)}"
        )


def run_poetry_build(staging: Path) -> List[Path]:
    """
    Run `poetry build` in the staging directory.

    Args:
        staging: Directory holding the staged distribution

    Returns:
        Paths of the built artifacts, wheel and sdist

    Raises:
        DistributionError: If Poetry is unavailable or the build fails
    """
    if shutil.which("poetry") is None:
        raise DistributionError("Poetry is not available on PATH")

    result = subprocess.run(
        ["poetry", "build"],
        cwd=staging,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DistributionError(
            f"poetry build failed:\n{result.stdout}{result.stderr}".rstrip()
        )

    artifacts = sorted((staging / "dist").glob("*"))
    if len(artifacts) != 2:
        names = ", ".join(path.name for path in artifacts) or "none"
        raise DistributionError(f"Expected a wheel and an sdist, got: {names}")

    return artifacts


def check_archives(
    distribution: Distribution,
    artifacts: Sequence[Path],
    staged: Dict[Path, Path],
) -> None:
    """
    Verify the built archives against what was staged.

    The wheel is checked file by file: poetry-core ships `*.py` under the
    declared packages by default and needs `include` for anything else, so a
    data file such as the bundled chat UI can be dropped without a word. The
    sdist is checked for the namespace fault only — its layout mirrors the
    staging directory, and rebuilding it into a wheel is what
    `scripts/dist/verify.py --sdist` does.

    Args:
        distribution: The distribution that was built
        artifacts: Paths of the built wheel and sdist
        staged: Mapping returned by `stage_sources`

    Raises:
        DistributionError: If an archive is malformed or incomplete
    """
    wheel = next((path for path in artifacts if path.suffix == ".whl"), None)
    sdist = next((path for path in artifacts if path.name.endswith(".tar.gz")), None)
    if wheel is None or sdist is None:
        raise DistributionError(f"{distribution.name}: build produced no wheel/sdist pair")

    with zipfile.ZipFile(wheel) as archive:
        wheel_paths = set(archive.namelist())

    check_namespace_dirs(distribution, wheel_paths, wheel.name)

    missing = sorted(
        str(relative) for relative in staged if str(relative) not in wheel_paths
    )
    if missing:
        listing = ", ".join(missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise DistributionError(
            f"{distribution.name}: {len(missing)} staged files are absent from "
            f"{wheel.name}: {listing}{more}"
        )

    with tarfile.open(sdist) as archive:
        # Entries are prefixed with the archive's root directory; the paths
        # checked here are the ones below `src/`.
        sdist_paths = {
            name.split("/src/", 1)[1] for name in archive.getnames() if "/src/" in name
        }

    check_namespace_dirs(distribution, sdist_paths, sdist.name)


def collect_artifacts(distribution: Distribution, artifacts: Sequence[Path]) -> List[Path]:
    """
    Move the built archives into the repository's `dist/` directory.

    Artifacts of earlier versions of the same distribution are removed first:
    both the verification step and the upload act on `dist/` as a whole, and a
    leftover wheel from a previous version would be installed or published
    alongside the current one.

    Args:
        distribution: The distribution that was built
        artifacts: Paths of the built wheel and sdist

    Returns:
        Paths of the artifacts in their final location
    """
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    for stale in DIST_DIR.glob(f"{distribution.file_prefix}-*"):
        stale.unlink()

    collected = []
    for artifact in artifacts:
        target = DIST_DIR / artifact.name
        shutil.copy2(artifact, target)
        collected.append(target)

    return collected


def build_distribution(distribution: Distribution) -> List[Path]:
    """
    Assemble, build and check one distribution.

    Args:
        distribution: The distribution to build

    Returns:
        Paths of its artifacts in `dist/`

    Raises:
        DistributionError: If staging, building or checking fails
    """
    print(
        f"[DIST] {distribution.name} {distribution.version}: "
        f"staging {', '.join(distribution.bundles)}"
    )

    if not LICENSE_FILE.is_file():
        raise DistributionError(f"{LICENSE_FILE} not found")

    # Named rather than left implicit: which README a distribution ships is the
    # difference between its PyPI page describing it and describing the whole
    # SDK, and that is worth seeing in the log of a release build.
    readme_relative = distribution.readme.relative_to(ROOT_DIR.resolve())
    print(f"  [OK] long description from {readme_relative}")

    with tempfile.TemporaryDirectory(prefix="aion-dist-") as tmp:
        staging = Path(tmp)
        if ROOT_DIR.resolve() in staging.resolve().parents:
            raise DistributionError(
                f"Staging directory {staging} is inside the working tree; "
                "set TMPDIR to a location outside the repository"
            )

        stage_pyproject(distribution, staging)

        base = readme_relative.parent
        (staging / "README.md").write_text(
            resolve_relative_links(
                distribution.readme.read_text(encoding="utf-8"),
                "" if base == Path(".") else base.as_posix(),
            ),
            encoding="utf-8",
        )
        shutil.copy2(LICENSE_FILE, staging / "LICENSE")

        staged = stage_sources(distribution, staging)
        check_namespace_dirs(
            distribution,
            [str(relative) for relative in staged],
            "the staged source tree",
        )
        print(f"  [OK] {len(staged)} files staged")

        artifacts = run_poetry_build(staging)
        check_archives(distribution, artifacts, staged)
        collected = collect_artifacts(distribution, artifacts)

    for artifact in collected:
        print(f"  [OK] {artifact.name}")

    return collected


def select(distributions: Sequence[Distribution], names: Sequence[str]) -> List[Distribution]:
    """
    Resolve the distributions named on the command line.

    Args:
        distributions: Every discovered distribution
        names: Names requested by the caller

    Returns:
        The requested distributions, in the order they were named

    Raises:
        DistributionError: If a name matches no distribution
    """
    by_name = {distribution.name: distribution for distribution in distributions}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        known = ", ".join(sorted(by_name))
        raise DistributionError(f"Unknown distribution(s): {', '.join(unknown)}. Known: {known}")
    return [by_name[name] for name in names]


def main() -> int:
    """
    Main entry point for the build script.

    Returns:
        0 if every requested distribution was built, 1 otherwise
    """
    parser = argparse.ArgumentParser(
        description="Build the publishable aionto-* distributions into dist/."
    )
    parser.add_argument("names", nargs="*", help="distributions to build")
    parser.add_argument("--all", action="store_true", help="build every distribution")
    args = parser.parse_args()

    try:
        distributions = find_distributions()

        if not args.all and not args.names:
            parser.error("name at least one distribution, or pass --all")

        check_version_lockstep(distributions)
        targets = distributions if args.all else select(distributions, args.names)
        version = distributions[0].version

        print(f"[INFO] Building {len(targets)} distributions at version {version}")

        artifacts: List[Path] = []
        for distribution in targets:
            artifacts.extend(build_distribution(distribution))
    except DistributionError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 1

    print(f"[COMPLETE] {len(artifacts)} artifacts in {DIST_DIR}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
