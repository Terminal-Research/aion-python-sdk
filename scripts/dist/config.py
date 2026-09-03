"""
Shared discovery and validation for the distribution build scripts.

The five published `aionto-*` distributions are not new source trees: each one
is a *fat* redistribution of libraries that live under `libs/`. What a
distribution carries is declared in its own `pyproject.toml`, in the
`[tool.aion-dist] bundles` table, so there is no central list here to keep in
sync — a distribution directory holding a `pyproject.toml` is a distribution,
exactly as `scripts/deps/config.py` treats `libs/`.

A distribution does not carry a version of its own. All five are released
together at one version — the version of `libs/aion-sdk`, the package at the
centre of the SDK — so a release is prepared by bumping that one file and never
by editing a second copy of the number that can drift from the first.

Because the version is derived, the distribution's `pyproject.toml` declares it
`dynamic` and leaves its `aionto-*` cross-dependencies unconstrained; the build
writes both into the staged copy it hands to Poetry.

Everything the build relies on is checked here rather than discovered halfway
through a build: that the declared bundles exist, that no library is claimed by
two distributions, and that the cross-dependencies between the five are left
for the build to pin. The five distributions form one namespace tree cut from
one commit; a version skew between them is never intentional.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"
DISTRIBUTIONS_DIR = ROOT_DIR / "distributions"
DIST_DIR = ROOT_DIR / "dist"
LICENSE_FILE = ROOT_DIR / "LICENSE"

# Distribution names published to PyPI carry this prefix; the internal library
# names (`aion-*`) never appear on the index.
DIST_PREFIX = "aionto-"

# Every distribution is released at this library's version. The five form one
# namespace tree cut from one commit, so they share a number, and it lives in
# the package at the centre of the SDK rather than in five places at once.
RELEASE_VERSION_LIBRARY = "aion-sdk"

# The package namespace is a PEP 420 implicit namespace at these levels: the
# distributions each contribute a different subtree beneath them, so an
# `__init__.py` at any of these levels would let whichever distribution is
# imported first shadow the others.
NAMESPACE_DIRS = ("aion", "aion/langgraph", "aion/adk")

# Name, optional extras, and the rest of a PEP 508 requirement string. The
# requirements written in these files are plain `name[extras]specifier` forms;
# anything more exotic is reported rather than silently mis-parsed.
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<specifier>.*)$"
)

# Deliberately narrower than PEP 440: releases here are plain three-part
# versions, optionally with a pre-release or post-release suffix.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+)?$")


class DistributionError(Exception):
    """Raised when the declared distributions are inconsistent or incomplete."""


@dataclass(frozen=True)
class Distribution:
    """
    One publishable `aionto-*` distribution.

    Attributes:
        name          — distribution name on PyPI, equal to its directory name
        version       — the release version, that of `libs/aion-sdk`
        directory     — `distributions/<name>/`
        pyproject     — path to that directory's `pyproject.toml`
        readme        — file shipped as the long description on PyPI
        bundles       — internal `libs/` packages whose sources it carries
        requirements  — every requirement it declares, required and optional
    """

    name: str
    version: str
    directory: Path
    pyproject: Path
    readme: Path
    bundles: Tuple[str, ...]
    requirements: Tuple[str, ...]

    @property
    def source_dirs(self) -> List[Path]:
        """`src/` directory of every bundled library, in declaration order."""
        return [LIBS_DIR / bundle / "src" for bundle in self.bundles]

    @property
    def file_prefix(self) -> str:
        """
        Prefix shared by the distribution's wheel and sdist file names.

        Both are named after the PEP 503 normalized project name with `-`
        replaced by `_`, which for these names is the name with underscores.
        """
        return self.name.replace("-", "_")


def parse_requirement(requirement: str) -> Tuple[str, str]:
    """
    Split a PEP 508 requirement into its project name and version specifier.

    Args:
        requirement: Requirement string, for example `aionto-sdk==0.1.0`

    Returns:
        Tuple of project name and specifier; the specifier is `""` when the
        requirement is unconstrained

    Raises:
        DistributionError: If the requirement is not in the simple
            `name[extras]specifier` form these files use
    """
    match = _REQUIREMENT_RE.match(requirement.strip())
    if not match:
        raise DistributionError(f"Cannot parse requirement: {requirement!r}")
    return match.group("name"), match.group("specifier").strip()


def read_library_version(bundle: str) -> str:
    """
    Read the version a `libs/` package declares for itself.

    Args:
        bundle: Directory name of the library under `libs/`

    Returns:
        The library's version string

    Raises:
        DistributionError: If the library has no `pyproject.toml`, declares no
            version, or declares one of an unsupported shape
    """
    pyproject = LIBS_DIR / bundle / "pyproject.toml"
    if not pyproject.is_file():
        raise DistributionError(f"Library {bundle!r} has no pyproject.toml under libs/")

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("tool", {}).get("poetry", {}).get("version") or data.get(
        "project", {}
    ).get("version")
    if not version:
        raise DistributionError(f"{pyproject}: declares no version")

    validate_version(version)
    return version


def resolve_readme(pyproject: Path, settings: Dict, bundles: Sequence[str]) -> Path:
    """
    Locate the file a distribution ships as its PyPI long description.

    A distribution that bundles exactly one library is a redistribution of that
    library, so by default it ships that library's README — the page describing
    it is already written. A distribution bundling several must say which page
    it ships with `[tool.aion-dist] readme`; `aionto-sdk` carries six libraries
    and fronts the whole SDK, so it names the repository README.

    Args:
        pyproject: The distribution's `pyproject.toml`, named in errors
        settings: Its `[tool.aion-dist]` table
        bundles: Libraries the distribution bundles

    Returns:
        Absolute path to the README

    Raises:
        DistributionError: If several libraries are bundled and no README is
            declared, or the declared path is absolute, escapes the repository,
            or names a file that does not exist
    """
    declared = settings.get("readme")
    if declared:
        relative = Path(declared)
    elif len(bundles) == 1:
        relative = Path("libs") / bundles[0] / "README.md"
    else:
        raise DistributionError(
            f"{pyproject}: bundles {len(bundles)} libraries, so it must name the page "
            "it ships in [tool.aion-dist].readme"
        )

    if relative.is_absolute():
        raise DistributionError(
            f"{pyproject}: [tool.aion-dist].readme must be relative to the repository root"
        )

    readme = (ROOT_DIR / relative).resolve()
    if ROOT_DIR.resolve() not in readme.parents:
        raise DistributionError(
            f"{pyproject}: [tool.aion-dist].readme {relative} escapes the repository"
        )
    if not readme.is_file():
        raise DistributionError(f"{pyproject}: readme {relative} not found")

    return readme


def read_distribution(pyproject: Path) -> Distribution:
    """
    Read one distribution's `pyproject.toml` and derive its release version.

    The version is not read from this file. It is the version of
    `libs/aion-sdk`, shared by every distribution, so this file must declare
    `version` dynamic and leave it out of `[project]` — a literal version here
    would be the second copy of the number that this arrangement exists to avoid.

    Args:
        pyproject: Path to `distributions/<name>/pyproject.toml`

    Returns:
        The parsed distribution

    Raises:
        DistributionError: If the file misses the tables the build needs,
            declares a name other than its directory's, or pins its own version
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    try:
        project = data["project"]
        name = project["name"]
    except KeyError as ex:
        raise DistributionError(f"{pyproject}: missing [project].{ex.args[0]}") from ex

    directory = pyproject.parent
    if name != directory.name:
        raise DistributionError(
            f"{pyproject}: project name {name!r} does not match directory {directory.name!r}"
        )

    if "version" in project:
        raise DistributionError(
            f"{pyproject}: declares [project].version; the version is that of "
            "libs/aion-sdk, so it must be listed in [project].dynamic instead"
        )
    if "version" not in project.get("dynamic", []):
        raise DistributionError(
            f"{pyproject}: must declare dynamic = [\"version\"], because the build "
            "supplies the version of libs/aion-sdk"
        )

    settings = data.get("tool", {}).get("aion-dist", {})

    bundles = settings.get("bundles")
    if not bundles:
        raise DistributionError(f"{pyproject}: missing or empty [tool.aion-dist].bundles")

    requirements = list(project.get("dependencies", []))
    for extra_requirements in project.get("optional-dependencies", {}).values():
        requirements.extend(extra_requirements)

    return Distribution(
        name=name,
        version=read_library_version(RELEASE_VERSION_LIBRARY),
        directory=directory,
        pyproject=pyproject,
        readme=resolve_readme(pyproject, settings, bundles),
        bundles=tuple(bundles),
        requirements=tuple(requirements),
    )


def find_distributions() -> List[Distribution]:
    """
    Discover and validate every distribution under `distributions/`.

    Validation covers what a build would otherwise discover late or not at all:
    a bundle naming a library that does not exist or carries no sources, and a
    library claimed by two distributions — which would publish the same modules
    twice and let installation order decide which copy wins.

    Returns:
        Distributions sorted by name

    Raises:
        DistributionError: If any distribution is inconsistent, or none exist
    """
    pyprojects = sorted(DISTRIBUTIONS_DIR.glob("*/pyproject.toml"))
    if not pyprojects:
        raise DistributionError(f"No distributions found under {DISTRIBUTIONS_DIR}")

    distributions = [read_distribution(path) for path in pyprojects]

    owners: Dict[str, str] = {}
    for distribution in distributions:
        for bundle in distribution.bundles:
            if not (LIBS_DIR / bundle / "pyproject.toml").is_file():
                raise DistributionError(
                    f"{distribution.name}: bundled package {bundle!r} not found under libs/"
                )
            if not (LIBS_DIR / bundle / "src" / "aion").is_dir():
                raise DistributionError(
                    f"{distribution.name}: bundled package {bundle!r} has no src/aion tree"
                )
            if bundle in owners:
                raise DistributionError(
                    f"Package {bundle!r} is bundled by both {owners[bundle]} and {distribution.name}"
                )
            owners[bundle] = distribution.name

    return distributions


def check_version_lockstep(distributions: Sequence[Distribution]) -> None:
    """
    Assert that the distributions can be released in lockstep.

    The five are released together at the version of `libs/aion-sdk`, so there
    is no second version to disagree with; what is checked here is the shape of
    the dependencies between them. They are left unconstrained in the committed
    files and pinned exactly in the staged copy at build time, when the version
    is known — a specifier written by hand would be a second copy of the number.

    Args:
        distributions: Every discovered distribution

    Raises:
        DistributionError: If a dependency on a sibling `aionto-*` distribution
            is unknown or already constrained
    """
    known = {distribution.name for distribution in distributions}

    for distribution in distributions:
        for requirement in distribution.requirements:
            name, specifier = parse_requirement(requirement)
            if not name.startswith(DIST_PREFIX):
                continue
            if name not in known:
                raise DistributionError(
                    f"{distribution.name}: depends on unknown distribution {name!r}"
                )
            if specifier:
                raise DistributionError(
                    f"{distribution.name}: dependency on {name} carries {specifier!r}; "
                    "sibling distributions must be left unconstrained, the build pins "
                    "them to the release version"
                )


def get_version(distributions: Sequence[Distribution]) -> str:
    """
    Return the single version the distributions are released at.

    Args:
        distributions: Every discovered distribution

    Returns:
        The shared version string

    Raises:
        DistributionError: If the distributions are not in lockstep
    """
    check_version_lockstep(distributions)
    return distributions[0].version


def validate_version(version: str) -> None:
    """
    Reject a library version that is not of the supported shape.

    Args:
        version: Version string read from a library's `pyproject.toml`

    Raises:
        DistributionError: If the version is not `X.Y.Z` with an optional
            pre-release or post-release suffix
    """
    if not _VERSION_RE.match(version):
        raise DistributionError(
            f"Version {version!r} is not of the form X.Y.Z[aN|bN|rcN|.postN]"
        )
