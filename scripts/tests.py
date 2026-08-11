#!/usr/bin/env python3
"""
Run pytest for all libs in the monorepo.

Discovers libs automatically from the libs/ directory: any subdirectory
matching aion-* that contains a pyproject.toml is included. Libs without
a tests/ directory are silently skipped.

Libs run concurrently. They are independent - a venv and a pytest process each -
and almost all of their wall time is importing rather than testing, so running
them one after another spends most of the run waiting on imports that could have
happened at the same time.

Anything after a bare ``--`` is handed to pytest untouched, so narrowing a run
does not mean going around this script and spelling out a lib's interpreter by
hand. Path arguments only make sense alongside a single lib; ``-k`` and friends
are fine across all of them.

Usage:
    python scripts/tests.py                        # run all libs
    python scripts/tests.py aion-core aion-db      # run specific libs
    python scripts/tests.py --fail-fast            # skip whatever has not started
    python scripts/tests.py --jobs 1               # one at a time, interleaved output
    python scripts/tests.py -- -k websocket        # forward arguments to pytest

Examples:
    $ make tests
    $ make tests ARGS="aion-sdk -- -k platform_link"
    $ python scripts/tests.py aion-core
    $ python scripts/tests.py aion-server --fail-fast
    $ python scripts/tests.py aion-sdk -- tests/handlers -q
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
LIBS_DIR = ROOT_DIR / "libs"


def discover_libs() -> list[str]:
    return sorted(
        d.name for d in LIBS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("aion-") and (d / "pyproject.toml").exists()
    )


def testable(lib_name: str) -> bool:
    """Report whether a lib has anything to run, saying why when it does not."""
    lib_dir = LIBS_DIR / lib_name

    if not (lib_dir / "pyproject.toml").exists():
        print(f"[SKIP] {lib_name}: no pyproject.toml")
        return False

    if not (lib_dir / "tests").exists():
        print(f"[SKIP] {lib_name}: no tests directory")
        return False

    return True


def build_command(lib_dir: Path) -> list[str]:
    """Address the lib's interpreter directly when there is one to address.

    ``poetry run`` starts a second Python before ours to work out the very path
    this reads off disk, which costs more than some of these suites take to run.
    Poetry remains the fallback for a lib whose venv has not been created yet.
    """
    venv_python = lib_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-m", "pytest"]
    return ["poetry", "run", "pytest"]


NO_TESTS_COLLECTED = 5
"""pytest's exit code for a run that matched nothing.

Not a failure - a ``-k`` expression aimed at one lib matches nothing in the other
nine - but not a pass either: reported as success it would hide the day a lib's
suite silently stops being collected.
"""


def run_tests(lib_name: str, pytest_args: list[str]) -> tuple[str, str]:
    """Run one lib's suite, returning an outcome and its whole output.

    The output is held rather than streamed: ten pytest processes writing to one
    terminal interleave into something no one can read.
    """
    lib_dir = LIBS_DIR / lib_name

    result = subprocess.run(
        build_command(lib_dir) + pytest_args,
        cwd=lib_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        outcome = "passed"
    elif result.returncode == NO_TESTS_COLLECTED:
        outcome = "no tests"
    else:
        outcome = "failed"

    return outcome, result.stdout + result.stderr


def report(lib_name: str, outcome: str, output: str) -> None:
    """Print one lib's run as a block of its own."""
    print(f"\n{'=' * 60}")
    print(f"[{outcome.upper()}] {lib_name}")
    print(f"{'=' * 60}")
    print(output.rstrip())


def main():
    args = sys.argv[1:]

    # Split on a bare "--" before reading anything else, so pytest's own flags
    # cannot be mistaken for this script's.
    if "--" in args:
        separator = args.index("--")
        args, pytest_args = args[:separator], args[separator + 1:]
    else:
        pytest_args = []

    fail_fast = "--fail-fast" in args
    jobs = None
    if "--jobs" in args:
        value = args[args.index("--jobs") + 1:]
        if not value or not value[0].isdigit():
            print("[ERROR] --jobs needs a number, e.g. --jobs 1")
            return 1
        jobs = int(value[0])
        args = [a for a in args if a != value[0]]
    libs = [a for a in args if not a.startswith("--")]

    if not LIBS_DIR.exists():
        print("[ERROR] libs/ directory not found")
        return 1

    all_libs = discover_libs()

    if not libs:
        libs = all_libs
    else:
        unknown = [l for l in libs if l not in all_libs]
        if unknown:
            print(f"[ERROR] Unknown libs: {', '.join(unknown)}")
            print(f"Available: {', '.join(all_libs)}")
            return 1

    libs = [lib for lib in libs if testable(lib)]
    if not libs:
        print("[SUMMARY] nothing to run")
        return 0

    results = {"passed": [], "failed": [], "no tests": [], "skipped": []}
    workers = jobs or min(len(libs), (os.cpu_count() or 4))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_tests, lib, pytest_args): lib for lib in libs}

        for future in as_completed(futures):
            lib = futures[future]

            if future.cancelled():
                results["skipped"].append(lib)
                continue

            outcome, output = future.result()
            report(lib, outcome, output)
            results[outcome].append(lib)

            if outcome == "failed" and fail_fast:
                # Whatever is already running is left to finish; there is no way
                # to un-start it, and killing it would only lose its output.
                cancelled = [f for f in futures if f.cancel()]
                if cancelled:
                    print(f"\n[FAIL-FAST] Skipping {len(cancelled)} lib(s) not yet started")

    print(f"\n{'=' * 60}")
    print("[SUMMARY] " + ", ".join(
        f"{len(libs)} {outcome}" for outcome, libs in results.items()))
    for outcome, libs in results.items():
        if libs:
            print(f"  {outcome}: {', '.join(sorted(libs))}")
    print(f"{'=' * 60}")

    return 1 if results["failed"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
