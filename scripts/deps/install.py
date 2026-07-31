#!/usr/bin/env python3
"""
Install dependencies for every package in the monorepo.

This script runs `poetry install --all-extras` on each discovered package to
install dependencies from its `poetry.lock`. Local path dependencies declared
with `develop = true` are installed in editable mode by Poetry itself, so no
separate editable-install step is needed.

Extras are included because they are how this monorepo wires optional siblings:
`aion-sdk` reaches `aion.server` only through `aion-server-langgraph` and
`aion-server-adk`, both of which sit behind extras. Installing without them
leaves `aion serve` unable to start. On packages that declare no extras the flag
is a no-op.

Usage:
    python install.py

Example:
    $ python scripts/deps/install.py
    [INFO] Installing: aion-api-client, aion-core, ...
    [PACKAGE] aion-api-client: installing dependencies
      [SUCCESS] Dependencies installed
    ...
"""

import sys

from config import get_all_packages
from package_ops import (
    check_poetry_available,
    execute_poetry_command,
    validate_libs_dir
)


def main():
    """
    Main entry point for the install script.

    Iterates through all packages and installs their production dependencies
    from lock files using `poetry install`.

    Returns:
        0 if all packages were installed successfully, 1 otherwise
    """
    if not validate_libs_dir():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    packages = get_all_packages()
    print(f"[INFO] Installing: {', '.join(packages)}")

    successful = 0
    failed = 0

    for package_name in packages:
        try:
            if execute_poetry_command(
                package_name,
                ['install', '--all-extras'],
                'installing dependencies',
                'Dependencies installed'
            ):
                successful += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            print(f"\n[WARNING] Interrupted while processing {package_name}")
            break
        except Exception as ex:
            print(f"[ERROR] Unexpected error processing {package_name}: {ex}")
            failed += 1

    print(f"\n[COMPLETE] Summary:")
    print(f"  - Successfully installed: {successful}")
    print(f"  - Failed: {failed}")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as ex:
        print(f"\n[ERROR] Unexpected error: {ex}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
