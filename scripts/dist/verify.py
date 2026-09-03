#!/usr/bin/env python3
"""
Verify the built `aionto-*` artifacts before they are published.

Two things are checked. `twine check` reads each archive's metadata the way
PyPI will, which is where a long description that does not render is caught -
PyPI rejects the upload, and an upload is not repeatable under the same
filename. Then the wheels are installed into throwaway environments from the
real index and imported, which is where a dependency that is missing from a
fat distribution's requirements shows up: the bundled sources import a package
that no longer has to be there, and nothing before this step would notice.

The two framework stacks are installed separately. Their constraints need not
co-resolve, and no user installs both: an environment holding LangGraph and
Google ADK together would be a stricter test than the product promises, and
its failures would not be release-blocking.

Usage:
    python verify.py                     # twine check + one environment per stack
    python verify.py --sdist             # additionally rebuild the sdists into a wheel
    python verify.py --python python3.12 # build the environments with another interpreter
    python verify.py --keep              # leave the environments on disk to inspect

Example:
    $ python scripts/dist/verify.py
    [STEP] twine check (10 artifacts)
      [OK] all archives passed
    [STEP] langgraph environment: aionto-sdk, aionto-langgraph-authoring, aionto-langgraph-server
      [OK] installed 87 packages
      [OK] imported 9 modules
      [OK] aion --help
    ...
    [COMPLETE] verified 10 artifacts in 2 environments
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from config import (
    DIST_DIR,
    Distribution,
    DistributionError,
    check_version_lockstep,
    find_distributions,
)

# Modules the fat SDK distribution is responsible for. Imported in every
# environment: a framework package that shadowed one of them, or a missing
# transitive requirement, has to fail here rather than at a user's first run.
SDK_MODULES = (
    "aion.core",
    "aion.api",
    "aion.db",
    "aion.mcp",
    "aion.server",
    "aion.proxy",
    "aion.cli",
)


@dataclass(frozen=True)
class SmokeEnvironment:
    """
    One throwaway environment: what is installed into it and what must import.

    Attributes:
        name           — environment name, used in output and on disk
        distributions  — distribution names installed from `dist/`
        modules        — modules imported after installation
    """

    name: str
    distributions: Tuple[str, ...]
    modules: Tuple[str, ...]


SMOKE_ENVIRONMENTS = (
    SmokeEnvironment(
        name="langgraph",
        distributions=(
            "aionto-sdk",
            "aionto-langgraph-authoring",
            "aionto-langgraph-server",
        ),
        modules=SDK_MODULES + ("aion.langgraph.authoring", "aion.langgraph.server"),
    ),
    SmokeEnvironment(
        name="adk",
        distributions=("aionto-sdk", "aionto-adk-authoring", "aionto-adk-server"),
        modules=SDK_MODULES + ("aion.adk.authoring", "aion.adk.server"),
    ),
)


def run(command: Sequence[str], description: str) -> str:
    """
    Run a command and fail loudly with its output.

    Args:
        command: Command and arguments
        description: What the command was doing, named in the error message

    Returns:
        The command's standard output

    Raises:
        DistributionError: If the command exits non-zero
    """
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as ex:
        raise DistributionError(f"{description} could not be started: {ex}") from ex

    if result.returncode != 0:
        raise DistributionError(
            f"{description} failed:\n{result.stdout}{result.stderr}".rstrip()
        )
    return result.stdout


def create_environment(python: str, root: Path) -> Path:
    """
    Create a virtual environment with the requested interpreter.

    Spawned rather than built through `venv.EnvBuilder`: the builder always
    produces an environment for the interpreter running this script, which
    would silently ignore `--python` and test the artifacts against the wrong
    Python version.

    Args:
        python: Interpreter the environment is built with
        root: Directory the environment is created in

    Returns:
        Path to the new environment's interpreter

    Raises:
        DistributionError: If the interpreter could not create the environment
    """
    run([python, "-m", "venv", str(root)], f"creating a virtual environment with {python}")
    return root / "bin" / "python"


def find_artifacts(
    distributions: Sequence[Distribution],
    suffix: str,
) -> List[Path]:
    """
    Locate one artifact of the given kind per distribution.

    The version is matched exactly, not as a prefix: `0.1.0` must not pick up a
    stale `0.1.0rc1` or `0.1.0.post1` left in `dist/` from an earlier build. A
    wheel name continues with `-` after the version (`name-version-py3-none-any`),
    an sdist ends right there with its suffix.

    Args:
        distributions: Distributions whose artifacts are wanted
        suffix: `.whl` or `.tar.gz`

    Returns:
        Artifact paths, in the order the distributions were given

    Raises:
        DistributionError: If an artifact is missing or ambiguous
    """
    artifacts = []
    for distribution in distributions:
        stem = f"{distribution.file_prefix}-{distribution.version}"
        pattern = f"{stem}-*{suffix}" if suffix == ".whl" else f"{stem}{suffix}"
        matches = sorted(DIST_DIR.glob(pattern))
        if not matches:
            raise DistributionError(
                f"No {suffix} for {distribution.name} {distribution.version} in {DIST_DIR}; "
                "run scripts/dist/build.py --all first"
            )
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches)
            raise DistributionError(f"Several {suffix} files for {distribution.name}: {names}")
        artifacts.append(matches[0])
    return artifacts


def resolve_twine(python: str, workspace: Path) -> List[str]:
    """
    Find a usable `twine`, installing one if the machine has none.

    Args:
        python: Interpreter used to build environments
        workspace: Directory a private environment may be created in

    Returns:
        The command prefix that runs twine

    Raises:
        DistributionError: If twine could not be installed
    """
    on_path = shutil.which("twine")
    if on_path:
        return [on_path]

    probe = subprocess.run(
        [python, "-m", "twine", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return [python, "-m", "twine"]

    print("  [INFO] twine not found, installing one into a temporary environment")
    interpreter = create_environment(python, workspace / "twine")
    run([str(interpreter), "-m", "pip", "install", "--quiet", "twine"], "twine install")
    return [str(interpreter), "-m", "twine"]


def check_metadata(twine: Sequence[str], artifacts: Sequence[Path]) -> None:
    """
    Run `twine check` over every artifact.

    Args:
        twine: Command prefix that runs twine
        artifacts: Every wheel and sdist to check

    Raises:
        DistributionError: If twine reports a problem
    """
    print(f"[STEP] twine check ({len(artifacts)} artifacts)")
    output = run(
        [*twine, "check", *[str(path) for path in artifacts]], "twine check"
    )
    if "FAILED" in output:
        raise DistributionError(f"twine check reported failures:\n{output.rstrip()}")
    print("  [OK] all archives passed")


def smoke_test(
    environment: SmokeEnvironment,
    distributions: Sequence[Distribution],
    python: str,
    workspace: Path,
    suffix: str,
) -> None:
    """
    Install one stack into a fresh environment and exercise it.

    Args:
        environment: The stack to install and what must import
        distributions: Every discovered distribution
        python: Interpreter the environment is built with
        workspace: Directory the environment is created in
        suffix: `.whl` to install the wheels, `.tar.gz` to rebuild the sdists

    Raises:
        DistributionError: If installation, an import, or the CLI fails
    """
    by_name = {distribution.name: distribution for distribution in distributions}
    selected = [by_name[name] for name in environment.distributions]
    artifacts = find_artifacts(selected, suffix)

    label = f"{environment.name} environment"
    if suffix != ".whl":
        label += " (from sdist)"
    print(f"[STEP] {label}: {', '.join(environment.distributions)}")

    root = workspace / f"{environment.name}{'-sdist' if suffix != '.whl' else ''}"
    interpreter = create_environment(python, root)

    run(
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            *[str(path) for path in artifacts],
        ],
        f"pip install into the {environment.name} environment",
    )

    installed = run(
        [str(interpreter), "-m", "pip", "list", "--format=freeze"], "pip list"
    )
    print(f"  [OK] installed {len(installed.splitlines())} packages")

    run(
        [str(interpreter), "-c", f"import {', '.join(environment.modules)}"],
        f"importing {len(environment.modules)} modules in the {environment.name} environment",
    )
    print(f"  [OK] imported {len(environment.modules)} modules")

    # Exercised through the installed console script rather than through
    # `python -m`: the script is generated by the wheel's entry point, and
    # `aion chat` shells out to the bundled cli.mjs, which only ships when the
    # distribution's `include` is right.
    run([str(root / "bin" / "aion"), "--help"], "aion --help")
    cli_js = next(root.glob("lib/python*/site-packages/aion/cli/bin/cli.mjs"), None)
    if cli_js is None:
        raise DistributionError(
            f"{environment.name} environment: aion/cli/bin/cli.mjs was not installed"
        )
    print("  [OK] aion --help, chat UI bundled")


def main() -> int:
    """
    Main entry point for the verification script.

    Returns:
        0 if every check passed, 1 otherwise
    """
    parser = argparse.ArgumentParser(
        description="Check the built aionto-* artifacts and smoke-test them in fresh environments."
    )
    parser.add_argument(
        "--sdist",
        action="store_true",
        help="additionally install one stack from the sdists, proving they rebuild",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter used to build the environments (default: this one)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the environments instead of deleting them",
    )
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="aion-verify-"))
    try:
        # Resolved before anything else, so a mistyped --python is one line
        # rather than a traceback out of the first subprocess that used it.
        python = shutil.which(args.python)
        if python is None:
            raise DistributionError(f"Interpreter {args.python!r} not found")

        distributions = find_distributions()
        check_version_lockstep(distributions)

        artifacts = find_artifacts(distributions, ".whl") + find_artifacts(
            distributions, ".tar.gz"
        )

        twine = resolve_twine(python, workspace)
        check_metadata(twine, artifacts)

        for environment in SMOKE_ENVIRONMENTS:
            smoke_test(environment, distributions, python, workspace, ".whl")

        if args.sdist:
            smoke_test(
                SMOKE_ENVIRONMENTS[0], distributions, python, workspace, ".tar.gz"
            )
    except DistributionError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"[INFO] environments left in {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)

    environments = len(SMOKE_ENVIRONMENTS) + (1 if args.sdist else 0)
    print(f"[COMPLETE] verified {len(artifacts)} artifacts in {environments} environments")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
