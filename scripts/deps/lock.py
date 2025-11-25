#!/usr/bin/env python3
"""
Lock dependencies for all packages in the monorepo.

This script runs `poetry lock` on all packages defined in config.PACKAGES
to update their poetry.lock files based on the current pyproject.toml specifications.

By default, poetry lock performs an incremental update, preserving existing
locked versions that still satisfy constraints. Use --regenerate to force
a complete rebuild of lock files from scratch.

Usage:
    python lock.py [--regenerate]

Options:
    --regenerate    Ignore existing lock files and regenerate from scratch

Examples:
    $ python scripts/deps/lock.py
    [INFO] Locking: aion-cli, aion-server, ...
    [PACKAGE] aion-cli: locking dependencies
      [SUCCESS] Lock file updated

    $ python scripts/deps/lock.py --regenerate
    [INFO] Regenerating lock files from scratch
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
    Main entry point for the lock script.

    Iterates through all packages and updates their lock files.

    Returns:
        0 if all packages were locked successfully, 1 otherwise
    """
    if not validate_libs_dir():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    # Check for --regenerate flag
    lock_command = ['lock']
    regenerate = '--regenerate' in sys.argv
    if regenerate:
        lock_command.append("--regenerate")
    action_desc = 'regenerating lock file' if regenerate else 'locking dependencies'

    packages = get_all_packages()

    if regenerate:
        print(f"[INFO] Regenerating lock files from scratch for: {', '.join(packages)}")
    else:
        print(f"[INFO] Locking: {', '.join(packages)}")

    successful = 0
    failed = 0

    for package_name in packages:
        try:
            if execute_poetry_command(
                package_name,
                lock_command,
                action_desc,
                'Lock file updated'
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
    print(f"  - Successfully locked: {successful}")
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
