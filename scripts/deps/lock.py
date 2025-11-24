#!/usr/bin/env python3
"""
Lock dependencies for all packages in the monorepo.

This script runs `poetry lock` on all packages defined in config.PACKAGES
to update their poetry.lock files based on the current pyproject.toml specifications.

Usage:
    python lock.py

Example:
    $ python scripts/deps/lock.py
    [INFO] Locking: aion-cli, aion-server, ...
    [PACKAGE] aion-cli: locking dependencies
      [SUCCESS] Lock file updated
    ...
"""

import subprocess
import sys
from pathlib import Path
from typing import Tuple

from config import PACKAGES, LIBS_DIR


def check_poetry_available() -> bool:
    """Check if Poetry is available on the system."""
    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def run_command(command: list, package_dir: Path, timeout: int = 300) -> Tuple[bool, str, str]:
    """
    Execute a shell command in a specific directory.

    Args:
        command: List of command arguments
        package_dir: Directory to run the command in
        timeout: Command timeout in seconds (default: 300)

    Returns:
        Tuple of (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            command,
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, "", str(e)


def lock_package(package_name: str) -> bool:
    """
    Lock dependencies for a single package.

    Runs `poetry lock` to update the poetry.lock file based on
    pyproject.toml specifications.

    Args:
        package_name: Name of the package to lock

    Returns:
        True if locking succeeded, False otherwise
    """
    package_dir = LIBS_DIR / package_name

    if not package_dir.exists():
        print(f"[SKIP] {package_name}: directory not found")
        return False

    if not (package_dir / 'pyproject.toml').exists():
        print(f"[SKIP] {package_name}: no pyproject.toml")
        return False

    print(f"\n[PACKAGE] {package_name}: locking dependencies")

    success, stdout, stderr = run_command(['poetry', 'lock'], package_dir)

    if not success:
        print(f"  [ERROR] Poetry lock failed")
        if stderr:
            print(f"  {stderr.strip()}")
        return False

    print(f"  [SUCCESS] Lock file updated")
    return True


def main():
    """
    Main entry point for the lock script.

    Iterates through all packages and updates their lock files.

    Returns:
        0 if all packages were locked successfully, 1 otherwise
    """
    if not LIBS_DIR.exists():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    print(f"[INFO] Locking: {', '.join(PACKAGES.keys())}")

    successful = 0
    failed = 0

    for package_name in PACKAGES.keys():
        try:
            if lock_package(package_name):
                successful += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            print(f"\n[WARNING] Interrupted while processing {package_name}")
            break
        except Exception as e:
            print(f"[ERROR] Unexpected error processing {package_name}: {e}")
            failed += 1

    print(f"\n[COMPLETE] Summary:")
    print(f"  - Successfully locked: {successful}")
    print(f"  - Failed: {failed}")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
