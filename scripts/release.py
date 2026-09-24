#!/usr/bin/env python3
"""Check a release of aionto-sdk locally, or publish one.

Two commands, one version. The version is ``[project].version`` in the root
``pyproject.toml`` and is read from there, never passed in: the tag, the
GitHub Release kind and every check below are derived from it.

``check``
    The local release gate - environment, unit tests, layer contract, build,
    packaging contract, smoke, scenarios - run in order, stopping at the first
    failure. Touches neither git nor GitHub, so it is safe to run on any
    branch at any time; ``make release-check`` is this.

``publish``
    Everything ``check`` does, preceded by a preflight over git, GitHub and
    PyPI and followed by a confirmation prompt, and then one action: create
    the GitHub Release whose ``py-v*`` tag starts ``publish-python.yml``. That
    workflow builds, checks and uploads to PyPI automatically when its
    build job succeeds. Nothing is uploaded from this machine, and the
    release is the last thing this script does - every step before it can
    fail without spending a tag or a version number.

Which of the two a version is - a final release or a pre-release - is read off
the version itself: an ``a``, ``b``, ``rc`` or ``.dev`` segment makes it a
pre-release, and the GitHub Release is marked as one. Nothing asks.

Run with the interpreter on the path, not through ``poetry run``: the script
only shells out to ``make``, ``git`` and ``gh`` and needs nothing from the
project's environment. Exit status is 0 when the command succeeded, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "pyproject.toml"

PROJECT_NAME = "aionto-sdk"
RELEASE_BRANCH = "main"
TAG_PREFIX = "py-v"
PUBLISH_WORKFLOW = "publish-python.yml"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PROJECT_NAME}/json"

# PEP 440 in its canonical spelling, and only that: ``poetry build`` writes the
# canonical form into the file names, and the tag is the manifest's text with
# a prefix, so anything Poetry would have normalised (``0.2.0-rc1``,
# ``0.2.0.RC1``) is refused here rather than silently producing a tag that no
# longer matches the artifacts. Local versions (``+abc``) cannot be uploaded to
# PyPI and are refused for the same reason.
CANONICAL_VERSION = re.compile(
    r"""
    ^
    (?:(?P<epoch>[1-9][0-9]*)!)?
    (?P<release>(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*)
    (?P<pre>(?:a|b|rc)(?:0|[1-9][0-9]*))?
    (?P<post>\.post(?:0|[1-9][0-9]*))?
    (?P<dev>\.dev(?:0|[1-9][0-9]*))?
    $
    """,
    re.VERBOSE,
)


class ReleaseError(Exception):
    """A condition that stops the release; its message is the whole report."""


@dataclass(frozen=True)
class Version:
    """A version the manifest may be released under, and what it implies.

    Attributes:
        text        — the version exactly as written in ``[project].version``.
        prerelease  — whether PEP 440 calls it a pre-release, which decides
                      whether the GitHub Release is flagged as one.
    """

    text: str
    prerelease: bool

    @property
    def tag(self) -> str:
        """The release tag: the version with the ``py-v`` prefix."""
        return f"{TAG_PREFIX}{self.text}"

    @property
    def kind(self) -> str:
        """The words used for the version wherever a person reads them."""
        return "pre-release" if self.prerelease else "final release"


def parse_version(text: str) -> Version:
    """Read ``text`` as a canonical PEP 440 version.

    Args:
        text: the manifest's ``[project].version``.

    Returns:
        The version and whether it is a pre-release.

    Raises:
        ReleaseError: when ``text`` is not a version in canonical spelling,
            with the two spellings people reach for named in the message.
    """
    match = CANONICAL_VERSION.match(text)
    if match is None:
        raise ReleaseError(
            f"[project].version {text!r} is not a canonical PEP 440 version: "
            "a pre-release is written 0.2.0rc1, 0.2.0b1 or 0.2.0a1 - no separator, "
            "no 'pr', no local suffix"
        )
    prerelease = match.group("pre") is not None or match.group("dev") is not None
    return Version(text=text, prerelease=prerelease)


def read_version(manifest: Path = MANIFEST) -> Version:
    """Read and parse the version from the root manifest."""
    with manifest.open("rb") as handle:
        text = tomllib.load(handle)["project"]["version"]
    return parse_version(text)


# --- running things -----------------------------------------------------------


def say(message: str) -> None:
    """Print a line of progress, flushed so it lands before subprocess output."""
    print(message, flush=True)


def run_step(name: str, argv: list[str]) -> None:
    """Run one step of the gate, letting its output through.

    Args:
        name: what the step is called in progress lines and in the failure.
        argv: the command to run, from the repository root.

    Raises:
        ReleaseError: when the command exits non-zero.
    """
    say(f"\n==> {name}: {' '.join(argv)}")
    completed = subprocess.run(argv, cwd=REPO_ROOT)
    if completed.returncode != 0:
        raise ReleaseError(f"step '{name}' failed with exit status {completed.returncode}")


def capture(argv: list[str]) -> str:
    """Run a command for its stdout, stripped; non-zero exit is an error."""
    completed = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseError(f"`{' '.join(argv)}` failed: {detail}")
    return completed.stdout.strip()


def succeeds(argv: list[str]) -> bool:
    """Whether a command exits zero; its output is discarded."""
    completed = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True)
    return completed.returncode == 0


# --- the gate -----------------------------------------------------------------


def make(target: str, *assignments: str) -> list[str]:
    """The argv for one Makefile target, with optional ``VAR=value`` pairs."""
    return ["make", "-C", str(REPO_ROOT), "--no-print-directory", target, *assignments]


def run_gate(python: str | None) -> None:
    """Run the release checks in order; the first failure stops the run.

    The steps are the Makefile's own targets, so what this runs and what a
    developer runs by hand are the same commands with the same definitions.

    The last step is the only one that runs the product rather than reading
    it: the scenario suite against the wheel the two steps above just built
    and checked.

    Args:
        python: interpreter for the clean environments, as ``smoke.py
            --python`` takes it (``3.12``, ``python3.12`` or a path); ``None``
            means the interpreter running this script.
    """
    run_step("environment", make("check-env"))
    run_step("unit tests", make("tests-unit"))
    run_step("layer contract", make("lint-imports"))
    run_step("build", make("dist-build"))
    run_step("packaging contract", make("dist-check"))
    smoke_args = [f"SMOKE_ARGS=--python {python}"] if python else []
    run_step("smoke", make("dist-smoke", *smoke_args))
    scenario_args = [f"SCENARIOS_ARGS=--python {python}"] if python else []
    run_step("scenarios", make("tests-scenarios-dist", *scenario_args))


# --- preflight ----------------------------------------------------------------


def pypi_has_version(version: Version) -> bool:
    """Whether PyPI already lists ``version`` for the project.

    A project with no releases yet answers 404 and that is "no". Any other
    failure to reach PyPI is an error rather than a "no": a release created
    while the answer is unknown could spend a tag on a version that is taken.
    """
    request = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise ReleaseError(f"PyPI answered {error.code} for {PYPI_JSON_URL}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ReleaseError(f"could not reach PyPI at {PYPI_JSON_URL}: {error}") from error
    return version.text in payload.get("releases", {})


def preflight(version: Version) -> str:
    """Check everything that must hold before a release is created.

    The checks are ordered from cheapest to slowest, and every one of them is
    about the state the release would be cut from: the tools, the working
    tree, the branch, the tag and the version number.

    Args:
        version: the version about to be released.

    Returns:
        The repository's URL on GitHub, for the closing hint.

    Raises:
        ReleaseError: naming the first condition that does not hold.
    """
    say("==> preflight")

    for tool in ("git", "gh", "make"):
        if shutil.which(tool) is None:
            raise ReleaseError(f"`{tool}` is not on the PATH")
    if not succeeds(["gh", "auth", "status"]):
        raise ReleaseError("`gh` is not logged in: run `gh auth login`")
    repo_url = capture(["gh", "repo", "view", "--json", "url", "--jq", ".url"])
    say(f"    gh: authenticated, repository {repo_url}")

    if capture(["git", "status", "--porcelain", "--untracked-files=no"]):
        raise ReleaseError("the working tree has uncommitted changes")
    branch = capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != RELEASE_BRANCH:
        raise ReleaseError(f"releases are cut from {RELEASE_BRANCH}; the checkout is on {branch}")
    capture(["git", "fetch", "--quiet", "origin", RELEASE_BRANCH])
    head = capture(["git", "rev-parse", "HEAD"])
    remote_head = capture(["git", "rev-parse", f"origin/{RELEASE_BRANCH}"])
    if head != remote_head:
        raise ReleaseError(
            f"HEAD ({head[:12]}) is not origin/{RELEASE_BRANCH} ({remote_head[:12]}): "
            "pull, or push, so that the tag lands on what is on GitHub"
        )
    say(f"    git: clean, on {RELEASE_BRANCH} at {head[:12]}, in sync with origin")

    tag = version.tag
    if succeeds(["git", "rev-parse", "--quiet", "--verify", f"refs/tags/{tag}"]):
        raise ReleaseError(f"tag {tag} already exists locally")
    if capture(["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"]):
        raise ReleaseError(f"tag {tag} already exists on origin")
    if succeeds(["gh", "release", "view", tag]):
        raise ReleaseError(f"GitHub Release {tag} already exists")
    say(f"    tag: {tag} is free")

    if pypi_has_version(version):
        raise ReleaseError(
            f"{PROJECT_NAME} {version.text} is already on PyPI; a version number is "
            "spent for good - bump [project].version and release the next one"
        )
    say(f"    pypi: {PROJECT_NAME} {version.text} is not published")
    return repo_url


# --- confirmation -------------------------------------------------------------


def confirm(version: Version, assume_yes: bool) -> None:
    """Ask before the one irreversible step, unless told not to.

    Args:
        version: what would be released, so the prompt says it.
        assume_yes: skip the prompt - for a terminal-less run that has decided
            already. Without it, a run that has no terminal to ask on is an
            error, not a silent yes.

    Raises:
        ReleaseError: when the answer is anything but yes, or when there is
            no terminal and ``assume_yes`` was not given.
    """
    if assume_yes:
        say(f"\n--yes given: releasing {version.text} without asking")
        return
    if not sys.stdin.isatty():
        raise ReleaseError("no terminal to confirm on; pass --yes to release non-interactively")
    prompt = (
        f"\nRelease {PROJECT_NAME} {version.text} as {version.tag} ({version.kind}). "
        "Are you sure? [y/N] "
    )
    answer = input(prompt).strip().lower()
    if answer not in ("y", "yes"):
        raise ReleaseError("not confirmed; nothing was released")


# --- commands -----------------------------------------------------------------


def announce(version: Version) -> None:
    """Print the version and what it will be released as."""
    say(f"{PROJECT_NAME} {version.text}: tag {version.tag}, {version.kind}")


def command_check(args: argparse.Namespace) -> None:
    """``check``: run the gate and report; nothing else."""
    version = read_version()
    announce(version)
    run_gate(args.python)
    say(f"\nrelease check passed for {version.text}; nothing was published")


def command_publish(args: argparse.Namespace) -> None:
    """``publish``: preflight, gate, confirm, create the release."""
    version = read_version()
    announce(version)
    repo_url = preflight(version)
    run_gate(args.python)
    confirm(version, args.yes)

    argv = [
        "gh", "release", "create", version.tag,
        "--target", RELEASE_BRANCH,
        "--title", version.tag,
        "--generate-notes",
    ]
    if version.prerelease:
        argv.append("--prerelease")
    run_step("create release", argv)

    say(
        f"\n{version.tag} is published as a GitHub Release. The workflow "
        f"'{PUBLISH_WORKFLOW}' is now building and checking it; if the build "
        "succeeds, the workflow uploads to PyPI automatically:\n"
        f"    {repo_url}/actions/workflows/{PUBLISH_WORKFLOW}\n"
        "A successful upload spends the version number for good - PyPI never "
        "takes a file name twice."
    )


def build_parser() -> argparse.ArgumentParser:
    """The command line: two subcommands sharing the smoke interpreter option."""
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Check a release of aionto-sdk locally, or publish one.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_python(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--python",
            help="interpreter for the smoke environments: 3.12, python3.12 or a path "
            "(default: the interpreter running this script)",
        )

    check = subcommands.add_parser(
        "check",
        help="run the release checks; publish nothing",
        description="Run environment, tests, layer, build, packaging and smoke "
        "checks in order. Touches neither git nor GitHub.",
    )
    add_python(check)
    check.set_defaults(handler=command_check)

    publish = subcommands.add_parser(
        "publish",
        help="check, confirm, and create the GitHub Release that publishes to PyPI",
        description="Preflight git, GitHub and PyPI, run the checks, ask, then "
        "create the py-v* GitHub Release that starts the publishing workflow.",
    )
    add_python(publish)
    publish.add_argument(
        "--yes",
        action="store_true",
        help="do not ask for confirmation (for a run without a terminal)",
    )
    publish.set_defaults(handler=command_publish)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: dispatch to the subcommand and turn failures into exit 1."""
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except ReleaseError as error:
        print(f"\nrelease: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nrelease: interrupted; nothing was released", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
