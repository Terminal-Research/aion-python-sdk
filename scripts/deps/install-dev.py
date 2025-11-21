#!/usr/bin/env python3
"""
Install local dependencies in editable mode for development.

This script installs local package dependencies in editable mode using pip,
allowing for live development across multiple packages in the monorepo.
It automatically resolves transitive dependencies in the correct order.

For example, if aion-cli depends on aion-server-langgraph, which depends on
aion-api-client and aion-shared, all dependencies will be installed in the
order: aion-server-langgraph, aion-api-client, aion-shared.

Usage:
    python install-dev.py

Example:
    $ python scripts/deps/install-dev.py
    [INFO] Available packages: aion-cli, aion-server-langgraph, ...
    [PACKAGE] aion-cli
      Direct dependencies: aion-server-langgraph
      Installing (with transitive): aion-server-langgraph, aion-api-client, aion-shared
      [SUCCESS] Installed aion-server-langgraph < /path/to/libs/aion-server-langgraph
      ...
"""

import os
import subprocess
import sys
from pathlib import Path

from config import PACKAGES, LIBS_DIR, resolve_dependencies


def check_poetry_available() -> bool:
    """Check if Poetry is available on the system and print version info."""
    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[INFO] Using {result.stdout.strip()}")
            return True
        return False
    except FileNotFoundError:
        return False


def ensure_poetry_environment(package_dir: Path) -> bool:
    """
    Ensure a Poetry environment exists for a package.

    If the environment doesn't exist or is broken, initializes it by running
    `poetry install`.

    Args:
        package_dir: Path to the package directory

    Returns:
        True if environment is ready, False otherwise
    """
    try:
        result = subprocess.run(
            ['poetry', 'env', 'info', '--path'],
            cwd=package_dir,
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            test_result = subprocess.run(
                ['poetry', 'run', 'python', '--version'],
                cwd=package_dir,
                capture_output=True,
                text=True
            )
            if test_result.returncode == 0:
                return True

        print(f"  [SETUP] Initializing Poetry environment for {package_dir.name}")
        init_result = subprocess.run(
            ['poetry', 'install'],
            cwd=package_dir,
            capture_output=True,
            text=True
        )
        return init_result.returncode == 0

    except Exception as e:
        print(f"  [ERROR] Failed to initialize Poetry environment: {e}")
        return False


def install_package_editable(dep_name: str, package_dir: Path) -> bool:
    """
    Install a local package in editable mode using pip.

    Runs `poetry run pip install -e <relative_path>` to install the dependency
    in editable/development mode, allowing live code changes.

    Args:
        dep_name: Name of the dependency package to install
        package_dir: Directory of the parent package where dependency will be installed

    Returns:
        True if installation succeeded, False otherwise
    """
    dep_path = LIBS_DIR / dep_name

    if not dep_path.exists():
        print(f"  [ERROR] Package directory not found: {dep_path}")
        return False

    if not (dep_path / 'pyproject.toml').exists():
        print(f"  [ERROR] No pyproject.toml in: {dep_path}")
        return False

    try:
        relative_path = os.path.relpath(dep_path, package_dir)
        cmd = ['poetry', 'run', 'pip', 'install', '-e', relative_path]

        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"  [SUCCESS] Installed {dep_name} < {dep_path}")
            return True
        else:
            print(f"  [ERROR] Failed to install {dep_name}: {result.stderr}")
            return False

    except Exception as e:
        print(f"  [ERROR] Error installing {dep_name}: {e}")
        return False


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
    package_dir = LIBS_DIR / package_name

    if not package_dir.exists():
        print(f"[SKIP] {package_name}: directory not found")
        return 0

    if not (package_dir / 'pyproject.toml').exists():
        print(f"[SKIP] {package_name}: no pyproject.toml")
        return 0

    # Resolve all transitive dependencies automatically
    try:
        all_deps = resolve_dependencies(package_name)
        # Remove the package itself from the list
        local_deps = [dep for dep in all_deps if dep != package_name]
    except ValueError as e:
        print(f"[ERROR] {package_name}: {e}")
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
    if not LIBS_DIR.exists():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    print(f"[INFO] Available packages: {', '.join(PACKAGES.keys())}")

    total_installed = 0
    for package_name in PACKAGES.keys():
        total_installed += process_package(package_name)

    print(f"\n[COMPLETE] Installed {total_installed} local dependencies")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
