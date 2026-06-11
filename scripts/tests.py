#!/usr/bin/env python3
"""
Run pytest for all libs in the monorepo.

Discovers libs automatically from the libs/ directory: any subdirectory
matching aion-* that contains a pyproject.toml is included. Libs without
a tests/ directory are silently skipped.

Usage:
    python scripts/tests.py                        # run all libs
    python scripts/tests.py aion-core aion-db      # run specific libs
    python scripts/tests.py --fail-fast            # stop on first failure

Examples:
    $ make tests
    $ python scripts/tests.py aion-core
    $ python scripts/tests.py aion-server --fail-fast
"""

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
LIBS_DIR = ROOT_DIR / "libs"

def discover_libs() -> list[str]:
    return sorted(
        d.name for d in LIBS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("aion-") and (d / "pyproject.toml").exists()
    )


def run_tests(lib_name: str) -> bool:
    lib_dir = LIBS_DIR / lib_name

    if not (lib_dir / "pyproject.toml").exists():
        print(f"\n[SKIP] {lib_name}: no pyproject.toml")
        return True

    if not (lib_dir / "tests").exists():
        print(f"\n[SKIP] {lib_name}: no tests directory")
        return True

    print(f"\n{'='*60}")
    print(f"[TEST] {lib_name}")
    print(f"{'='*60}")

    result = subprocess.run(
        ["poetry", "run", "pytest"],
        cwd=lib_dir,
    )
    return result.returncode == 0


def main():
    args = sys.argv[1:]
    fail_fast = "--fail-fast" in args
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

    passed, failed = [], []

    for lib in libs:
        ok = run_tests(lib)
        (passed if ok else failed).append(lib)
        if not ok and fail_fast:
            print(f"\n[FAIL-FAST] Stopping after {lib}")
            break

    print(f"\n{'='*60}")
    print(f"[SUMMARY] {len(passed)} passed, {len(failed)} failed")
    if passed:
        print(f"  passed: {', '.join(passed)}")
    if failed:
        print(f"  failed: {', '.join(failed)}")
    print(f"{'='*60}")

    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
