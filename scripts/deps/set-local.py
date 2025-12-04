#!/usr/bin/env python3
"""
Toggle between remote and local path dependencies in pyproject.toml files.

This script helps test local changes without committing by switching aion-*
dependencies between remote git references and local relative paths. It preserves
the original configuration using comments for easy restoration.

Usage:
    # Switch to local relative paths
    python scripts/deps/set-local.py apply

    # Restore original dependencies
    python scripts/deps/set-local.py revert
"""

import argparse
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"

# Comment markers to track conversions
LOCAL_MARKER = "# [LOCAL-DEP]"
ORIGINAL_MARKER = "# [ORIGINAL-DEP]"

# Regex patterns
GIT_DEP_PATTERN = re.compile(
    r'^(?P<name>aion-[\w-]+)\s*=\s*\{\s*'
    r'git\s*=\s*"[^"]*Terminal-Research/aion-python-sdk[^"]*",\s*'
    r'branch\s*=\s*"(?P<branch>[^"]+)",\s*'
    r'subdirectory\s*=\s*"(?P<subdirectory>[^"]+)"'
    r'.*?\}$'
)

LOCAL_DEP_PATTERN = re.compile(
    r'\s*(?P<name>aion-[\w-]+)\s*=\s*\{\s*path\s*=\s*"(?P<path>[^"]+)"'
)


@dataclass
class DependencyInfo:
    """Information about a dependency extracted from TOML."""
    name: str
    extras: Optional[list] = None
    optional: bool = False


def find_pyproject_files() -> List[Path]:
    """Find all pyproject.toml files in libs directory."""
    return list(LIBS_DIR.glob("*/pyproject.toml"))


def parse_dependencies(file_path: Path) -> dict:
    """
    Parse pyproject.toml and extract dependencies using tomllib.

    Returns:
        Dictionary mapping dependency names to their configuration
    """
    try:
        with open(file_path, 'rb') as f:
            data = tomllib.load(f)
        return data.get('tool', {}).get('poetry', {}).get('dependencies', {})
    except Exception as e:
        print(f"  [WARNING] Failed to parse TOML: {e}")
        return {}


def get_dependency_info(dep_name: str, dependencies: dict) -> DependencyInfo:
    """Extract dependency information from parsed TOML."""
    dep_config = dependencies.get(dep_name, {})

    if isinstance(dep_config, dict):
        return DependencyInfo(
            name=dep_name,
            extras=dep_config.get('extras'),
            optional=dep_config.get('optional', False)
        )

    return DependencyInfo(name=dep_name)


def get_indent(line: str) -> int:
    """Get indentation level of a line."""
    return len(line) - len(line.lstrip())


def format_extras(extras: list) -> str:
    """Format extras list for TOML output."""
    return str(extras).replace("'", '"')


def build_local_dependency_line(
    dep_info: DependencyInfo,
    relative_path: str,
    indent: int
) -> str:
    """Build a local dependency line with proper formatting."""
    parts = [f'{dep_info.name} = {{ path = "{relative_path}"']

    if dep_info.extras:
        parts.append(f'extras = {format_extras(dep_info.extras)}')

    if dep_info.optional:
        parts.append('optional = true')

    return ' ' * indent + ', '.join(parts) + ' }'


def convert_git_to_local(
    line: str,
    file_path: Path,
    dependencies: dict
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Convert a single git dependency line to local path.

    Returns:
        Tuple of (new_lines, change_description) or (None, None) if no conversion needed
    """
    match = GIT_DEP_PATTERN.match(line.strip())
    if not match:
        return None, None

    dep_name = match.group('name')
    branch = match.group('branch')
    subdirectory = match.group('subdirectory')

    # Check if local path exists
    dep_absolute_path = ROOT_DIR / subdirectory
    if not dep_absolute_path.exists():
        print(f"  [WARNING] Local path not found, skipping: {dep_absolute_path}")
        return None, None

    # Get dependency metadata
    dep_info = get_dependency_info(dep_name, dependencies)

    # Calculate relative path
    relative_path = os.path.relpath(dep_absolute_path, file_path.parent)

    # Build new lines
    indent = get_indent(line)
    new_lines = [
        ' ' * indent + f'{ORIGINAL_MARKER} {line.strip()}',
        ' ' * indent + LOCAL_MARKER,
        build_local_dependency_line(dep_info, relative_path, indent)
    ]

    change_desc = f"  {dep_name}: {branch} -> {relative_path}"

    return new_lines, change_desc


def apply_local_paths(file_path: Path) -> Tuple[int, List[str]]:
    """
    Convert remote dependencies to local relative path dependencies.

    Returns:
        Tuple of (number of changes, list of change descriptions)
    """
    dependencies = parse_dependencies(file_path)
    content = file_path.read_text()
    lines = content.split('\n')

    new_lines = []
    changes = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip if already converted (has LOCAL_MARKER above)
        if i > 0 and LOCAL_MARKER in lines[i - 1]:
            new_lines.append(line)
            i += 1
            continue

        # Try to convert git dependency to local
        converted, change_desc = convert_git_to_local(line, file_path, dependencies)

        if converted:
            new_lines.extend(converted)
            changes.append(change_desc)
        else:
            new_lines.append(line)

        i += 1

    # Write changes if any
    if changes:
        file_path.write_text('\n'.join(new_lines))

    return len(changes), changes


def extract_local_dependency_info(lines: List[str], idx: int) -> Optional[Tuple[str, str, int]]:
    """
    Extract information from a commented local dependency block.

    Returns:
        Tuple of (original_line, dep_name, local_path, indent) or None
    """
    if ORIGINAL_MARKER not in lines[idx]:
        return None

    # Check if next two lines form a valid local dependency block
    if idx + 2 >= len(lines) or LOCAL_MARKER not in lines[idx + 1]:
        return None

    original_line = lines[idx].replace(ORIGINAL_MARKER, '').strip()
    local_line = lines[idx + 2]

    # Extract dependency info from local line
    match = LOCAL_DEP_PATTERN.match(local_line)
    if not match:
        return None

    dep_name = match.group('name')
    local_path = match.group('path')
    indent = get_indent(local_line)

    return original_line, dep_name, local_path, indent


def revert_to_original(file_path: Path) -> Tuple[int, List[str]]:
    """
    Restore original dependencies from commented versions.

    Returns:
        Tuple of (number of changes, list of change descriptions)
    """
    content = file_path.read_text()
    lines = content.split('\n')

    new_lines = []
    changes = []
    i = 0

    while i < len(lines):
        # Try to extract local dependency block
        dep_info = extract_local_dependency_info(lines, i)

        if dep_info:
            original_line, dep_name, local_path, indent = dep_info

            # Restore original line
            new_lines.append(' ' * indent + original_line)
            changes.append(f"  {dep_name}: {local_path} -> original")

            # Skip the marker and local dependency lines
            i += 3
        else:
            new_lines.append(lines[i])
            i += 1

    # Write changes if any
    if changes:
        file_path.write_text('\n'.join(new_lines))

    return len(changes), changes


def process_packages(mode: str) -> int:
    """
    Process all packages based on the specified mode.

    Args:
        mode: Either 'apply' or 'revert'

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Validate environment
    if not LIBS_DIR.exists():
        print(f"[ERROR] libs/ directory not found: {LIBS_DIR}")
        return 1

    pyproject_files = find_pyproject_files()
    if not pyproject_files:
        print("[ERROR] No pyproject.toml files found in libs/")
        return 1

    # Determine action function and messages
    if mode == 'apply':
        action_func = apply_local_paths
        action_msg = "Applying local path dependencies"
        success_msg = "Applied {count} local path dependency(ies)"
        updated_label = "UPDATED"
        help_msg = "Run 'python scripts/deps/set-local.py revert' to restore original dependencies"
    else:  # revert
        action_func = revert_to_original
        action_msg = "Reverting to original dependencies"
        success_msg = "Reverted {count} dependency(ies) to original"
        updated_label = "RESTORED"
        help_msg = None

    # Process packages
    print(f"[INFO] {action_msg}")
    print()

    total_changes = 0
    for file_path in sorted(pyproject_files):
        package_name = file_path.parent.name
        num_changes, changes = action_func(file_path)

        if num_changes > 0:
            print(f"[{updated_label}] {package_name}")
            for change in changes:
                print(change)
            total_changes += num_changes
        else:
            print(f"[SKIP] {package_name} (no changes needed)")

    # Print summary
    print()
    if total_changes > 0:
        print(f"[COMPLETE] {success_msg.format(count=total_changes)}")
        if help_msg:
            print(f"[INFO] {help_msg}")
    else:
        print("[COMPLETE] No changes needed")

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Toggle between remote and local path dependencies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Switch to local relative path dependencies
  python scripts/deps/set-local.py apply

  # Restore original dependencies
  python scripts/deps/set-local.py revert
        """
    )

    parser.add_argument(
        'mode',
        choices=['apply', 'revert'],
        help='Apply local paths or revert to original dependencies'
    )

    args = parser.parse_args()
    return process_packages(args.mode)


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
