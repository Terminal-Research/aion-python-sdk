#!/usr/bin/env python3
"""
Install production dependencies for all packages in the monorepo.

This script runs `poetry install --sync` on all packages defined in config.PACKAGES
to install dependencies from their poetry.lock files, removing any packages
not specified in the lock file.

Usage:
    python install.py

Example:
    $ python scripts/deps/install.py
    [INFO] Installing: aion-cli, aion-server, ...
    [PACKAGE] aion-cli: installing dependencies
      [SUCCESS] Dependencies installed
    ...
"""

import sys

from package_ops import (
    check_poetry_available,
    execute_poetry_command,
    get_all_packages,
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
                ['install'],
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
