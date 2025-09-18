#!/usr/bin/env python3
"""
Script to update all packages in libs/ directory by running poetry lock and poetry install.
This ensures all dependencies are updated and lock files are synchronized.
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Tuple


def check_poetry_available() -> bool:
    """Check if Poetry is available in the system."""
    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def find_package_directories(libs_dir: Path) -> List[Path]:
    """Find all directories in libs/ that contain pyproject.toml files."""
    packages = []

    if not libs_dir.exists():
        return packages

    for item in libs_dir.iterdir():
        if item.is_dir() and (item / 'pyproject.toml').exists():
            packages.append(item)

    return sorted(packages)


def run_poetry_command(command: List[str], package_dir: Path) -> Tuple[bool, str, str]:
    """Run a poetry command in the specified directory."""
    try:
        result = subprocess.run(
            command,
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out after 5 minutes"
    except Exception as e:
        return False, "", str(e)


def update_package_dependencies(package_dir: Path) -> bool:
    """Update dependencies for a single package using poetry lock and install."""
    package_name = package_dir.name
    print(f"\n[PACKAGE] {package_name}: updating dependencies")

    # Step 1: Run poetry lock
    print(f"[STEP 1/2] Running poetry lock...")
    success, stdout, stderr = run_poetry_command(['poetry', 'lock'], package_dir)

    if not success:
        print(f"[ERROR] Poetry lock failed for {package_name}")
        if stderr:
            print(f"[STDERR] {stderr.strip()}")
        return False

    if stdout.strip():
        print(f"[STDOUT] {stdout.strip()}")

    # Step 2: Run poetry install
    print(f"[STEP 2/2] Running poetry install...")
    success, stdout, stderr = run_poetry_command(['poetry', 'install'], package_dir)

    if not success:
        print(f"[ERROR] Poetry install failed for {package_name}")
        if stderr:
            print(f"[STDERR] {stderr.strip()}")
        return False

    if stdout.strip():
        print(f"[STDOUT] {stdout.strip()}")

    print(f"[SUCCESS] {package_name}: dependencies updated successfully")
    return True


def main():
    """Main function to update all packages in libs/ directory."""
    current_dir = Path.cwd()
    libs_dir = current_dir / 'libs'

    # Check if libs directory exists
    if not libs_dir.exists():
        print("[ERROR] libs/ directory not found")
        return 1

    # Check if Poetry is available
    if not check_poetry_available():
        print("[ERROR] Poetry is not available. Please install Poetry first.")
        return 1

    # Find all packages
    package_dirs = find_package_directories(libs_dir)

    if not package_dirs:
        print("[ERROR] No packages with pyproject.toml found in libs/")
        return 1

    package_names = [pkg.name for pkg in package_dirs]
    print(f"[INFO] Found {len(package_dirs)} packages: {', '.join(package_names)}")

    # Process each package
    successful_updates = 0
    failed_updates = 0

    for package_dir in package_dirs:
        try:
            if update_package_dependencies(package_dir):
                successful_updates += 1
            else:
                failed_updates += 1
        except KeyboardInterrupt:
            print(f"\n[WARNING] Interrupted while processing {package_dir.name}")
            break
        except Exception as e:
            print(f"[ERROR] Unexpected error processing {package_dir.name}: {e}")
            failed_updates += 1

    # Summary
    print(f"\n[COMPLETE] Update summary:")
    print(f"  - Successfully updated: {successful_updates} packages")
    print(f"  - Failed updates: {failed_updates} packages")
    print(f"  - Total processed: {successful_updates + failed_updates} packages")

    return 0 if failed_updates == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        sys.exit(1)
