#!/usr/bin/env python3
"""
Update git branch references in all pyproject.toml files.

This script helps manage branch references when testing feature branches.
When working on a feature branch, all internal dependencies should reference
the same branch to ensure consistent testing.

Usage:
    # Update all branches to feature branch for testing
    python scripts/deps/set-branch.py fixes/resolve_packages_installation

    # Restore all branches to main before merging
    python scripts/deps/set-branch.py main
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"


def find_pyproject_files() -> List[Path]:
    """Find all pyproject.toml files in libs directory."""
    return list(LIBS_DIR.glob("*/pyproject.toml"))


def update_branch_in_file(file_path: Path, target_branch: str) -> Tuple[int, List[str]]:
    """
    Update git branch references in a pyproject.toml file.

    Args:
        file_path: Path to pyproject.toml file
        target_branch: Branch name to set

    Returns:
        Tuple of (number of changes, list of changes made)
    """
    content = file_path.read_text()
    original_content = content
    changes = []

    # Pattern to match git dependencies with branch
    # Matches: aion-package = { git = "...", branch = "old-branch", subdirectory = "libs/aion-package" }
    pattern = r'(aion-[\w-]+)\s*=\s*\{\s*git\s*=\s*"[^"]*Terminal-Research/aion-python-sdk[^"]*",\s*branch\s*=\s*"([^"]+)"'

    def replace_branch(match):
        full_match = match.group(0)
        dependency_name = match.group(1)
        old_branch = match.group(2)

        if old_branch != target_branch:
            changes.append(f"  {dependency_name}: {old_branch} > {target_branch}")

        # Replace only the branch value in the matched string
        return full_match.replace(f'branch = "{old_branch}"', f'branch = "{target_branch}"')

    content = re.sub(pattern, replace_branch, content)

    if content != original_content:
        file_path.write_text(content)
        return len(changes), changes

    return 0, []


def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: python set-branch.py <branch-name>")
        print("\nExamples:")
        print("  python set-branch.py fixes/my-feature")
        print("  python set-branch.py main")
        return 1

    target_branch = sys.argv[1]

    if not LIBS_DIR.exists():
        print(f"[ERROR] libs/ directory not found: {LIBS_DIR}")
        return 1

    print(f"[INFO] Updating git branch references to: {target_branch}")
    print()

    pyproject_files = find_pyproject_files()
    total_changes = 0

    for file_path in sorted(pyproject_files):
        package_name = file_path.parent.name
        num_changes, changes = update_branch_in_file(file_path, target_branch)

        if num_changes > 0:
            print(f"[UPDATED] {package_name}")
            for change in changes:
                print(change)
            total_changes += num_changes
        else:
            print(f"[SKIP] {package_name} (no changes)")

    print()
    if total_changes > 0:
        print(f"[COMPLETE] Updated {total_changes} branch reference(s)")
    else:
        print("[COMPLETE] No changes needed")

    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as ex:
        print(f"\n[ERROR] {ex}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
