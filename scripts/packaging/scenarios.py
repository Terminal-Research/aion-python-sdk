#!/usr/bin/env python3
"""Run the scenario suite against the built wheel, in a clean virtual environment.

``smoke.py`` asks whether the distribution installs and its entry points work.
This asks the next question: whether an agent written against that
installation still behaves the way the scenarios say it does. It installs
``dist/*.whl`` with both framework extras into a venv that inherits nothing,
and runs ``tests/scenarios`` with the harness pointed at that venv's ``aion``.

So the halves come from different places on purpose:

*From this project's environment* — pytest, the A2A client, the harness. They
are tooling, not the subject; running them from the wheel would prove nothing
about the wheel and would need test dependencies inside it.

*From the clean venv* — ``aion serve``, every ``aion.*`` import an agent makes,
and every third-party library the extras bring. That is the installation a
user gets, and it is the one under test.

Run through ``poetry run`` (``make tests-scenarios-dist`` does): the interpreter
running this script is the one that runs pytest. The venv is built by
``--python`` or by that same interpreter, and is emptied afterwards unless
``--keep`` says otherwise.

Exit status is pytest's.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCENARIOS = "tests/scenarios"
DEFAULT_MARKERS = "scenario and not persistence"
"""Persistence needs a database of its own; `make tests-scenarios-persistence` runs it."""

AION_BIN_VAR = "SCENARIOS_AION_BIN"
"""What the harness reads to decide which `aion` to start."""

EXTRAS = ("langgraph-server", "adk-server")
"""Both frameworks in one environment, since one pytest run drives both."""


def _load_smoke():
    """Import ``smoke.py`` beside this file.

    By path rather than by name: ``scripts/packaging`` is a directory of
    scripts, not an importable package, and what is wanted from it is the two
    functions that already know how to find the artifacts and build a venv.
    """
    spec = importlib.util.spec_from_file_location("smoke", Path(__file__).parent / "smoke.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def build_parser() -> argparse.ArgumentParser:
    """The command line: where the wheel is, what builds the venv, what is kept."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Anything this does not recognise is passed to pytest untouched.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dist",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory holding the built distributions (default: ./dist)",
    )
    parser.add_argument(
        "--python",
        help="interpreter to build the environment with: 3.12, python3.12 or a path "
        "(default: the interpreter running this script)",
    )
    parser.add_argument(
        "-m",
        "--markers",
        default=DEFAULT_MARKERS,
        help=f"marker expression to select scenarios with (default: {DEFAULT_MARKERS!r})",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the environment behind for inspection",
    )
    return parser


def run_scenarios(venv_python: Path, markers: str, pytest_args: list[str]) -> int:
    """Run the suite from this environment, against the installation in ``venv_python``.

    Args:
        venv_python: the clean environment's interpreter; its ``aion`` is what
            the harness starts.
        markers: the marker expression passed to ``pytest -m``.
        pytest_args: further arguments, straight through.

    Returns:
        Pytest's exit status.
    """
    environment = dict(os.environ)
    environment[AION_BIN_VAR] = str(venv_python.parent / "aion")
    argv = [sys.executable, "-m", "pytest", SCENARIOS, "-m", markers, *pytest_args]
    print(f"\n==> {' '.join(argv)}\n    {AION_BIN_VAR}={environment[AION_BIN_VAR]}", flush=True)
    return subprocess.run(argv, cwd=REPO_ROOT, env=environment).returncode


def main(argv: list[str] | None = None) -> int:
    """Build the environment, run the scenarios in it, take it away again.

    Arguments this script does not define are pytest's, wherever they appear:
    ``--keep -k smoke`` and ``-x --python 3.12`` both work, and an option
    neither side knows is refused by pytest, which names it.
    """
    args, pytest_args = build_parser().parse_known_args(argv)

    if not args.dist.is_dir():
        print(f"{args.dist} does not exist: run `make dist-build` first", file=sys.stderr)
        return 1

    python = smoke.resolve_python(args.python)
    wheel = smoke.find_artifacts(args.dist)["wheel"]
    environment = smoke.Environment(name="scenarios", artifact="wheel", extras=EXTRAS)
    print(f"installing {wheel.name}[{','.join(EXTRAS)}] with {python}", flush=True)

    root = Path(tempfile.mkdtemp(prefix="aion-scenarios-dist-"))
    try:
        try:
            venv_python = smoke.create_environment(python, root, environment, wheel)
        except RuntimeError as error:
            print(f"could not prepare the environment:\n{error}", file=sys.stderr)
            return 1
        return run_scenarios(venv_python, args.markers, pytest_args)
    finally:
        if args.keep:
            print(f"\nenvironment left in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
