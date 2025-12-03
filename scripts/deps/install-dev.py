#!/usr/bin/env python3
"""
Install local dependencies in editable mode for development.

This script installs local package dependencies in editable mode using pip,
allowing for live development across multiple packages in the monorepo.
It automatically resolves transitive dependencies in the correct order.

For example, if aion-cli depends on aion-server, which depends on
aion-api-client and aion-shared, all dependencies will be installed in the
order: aion-server, aion-api-client, aion-shared.

Usage:
    python install-dev.py

Example:
    $ python scripts/deps/install-dev.py
    [INFO] Available packages: aion-cli, aion-server, ...
    [PACKAGE] aion-cli
      Direct dependencies: aion-server
      Installing (with transitive): aion-server, aion-api-client, aion-shared
      [SUCCESS] Installed aion-server < /path/to/libs/aion-server
      ...
"""

import sys

from config import PACKAGES, resolve_dependencies
from package_ops import (
    check_poetry_available,
    ensure_poetry_environment,
    install_package_editable,
    validate_package,
    get_all_packages,
    validate_libs_dir
)


def process_package(package_name: str) -> int:
    """
    Process a single package and install all its local dependencies.

    Automatically resolves all transitive dependencies and installs them
    in the correct order (high-level to low-level) in editable mode.

    Args:
        package_name: Name of the package to process

    Returns:
        Number of dependencies successfully installed
    """
    is_valid, package_dir = validate_package(package_name)

    if not is_valid:
        print(f"[SKIP] {package_name}: invalid package")
        return 0

    # Resolve all transitive dependencies automatically
    try:
        all_deps = resolve_dependencies(package_name)
        # Remove the package itself from the list
        local_deps = [dep for dep in all_deps if dep != package_name]
    except ValueError as ex:
        print(f"[ERROR] {package_name}: {ex}")
        return 0

    if not local_deps:
        return 0

    if not ensure_poetry_environment(package_dir):
        print(f"[ERROR] {package_name}: Poetry environment not available")
        return 0

    direct_deps = PACKAGES.get(package_name, [])
    direct_deps_str = ', '.join(direct_deps) if direct_deps else 'none'
    all_deps_str = ', '.join(local_deps)

    print(f"\n[PACKAGE] {package_name}")
    print(f"  Direct dependencies: {direct_deps_str}")
    print(f"  Installing (with transitive): {all_deps_str}")

    installed_count = 0
    for dep_name in local_deps:
        if install_package_editable(dep_name, package_dir):
            installed_count += 1

    return installed_count


def main():
    """
    Main entry point for the install-dev script.

    Iterates through all packages and installs their local dependencies
    in editable mode for development. Automatically resolves and installs
    transitive dependencies in the correct order.

    Returns:
        0 on success, 1 on error
    """
    if not validate_libs_dir():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    packages = get_all_packages()
    print(f"[INFO] Available packages: {', '.join(packages)}")

    total_installed = 0
    for package_name in packages:
        total_installed += process_package(package_name)

    print(f"\n[COMPLETE] Installed {total_installed} local dependencies")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as ex:
        print(f"\n[ERROR] Error: {ex}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
