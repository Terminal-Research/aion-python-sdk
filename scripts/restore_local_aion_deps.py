#!/usr/bin/env python3
"""
Script to restore original dependencies by uninstalling editable local packages
and running poetry install to restore original versions from pyproject.toml.
"""

import sys
import subprocess
from pathlib import Path
from typing import Dict, Set, Union

# Try to import tomllib (Python 3.11+) or fall back to tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def load_pyproject(pyproject_path: Path) -> Dict:
    """Load and parse pyproject.toml file."""
    try:
        if tomllib:
            with open(pyproject_path, 'rb') as f:
                return tomllib.load(f)
        else:
            return parse_simple_toml(pyproject_path)
    except Exception as e:
        print(f"Error loading {pyproject_path}: {e}")
        return {}


def parse_simple_toml(toml_path: Path) -> Dict:
    """Simple TOML parser fallback for basic pyproject.toml files."""
    result = {}
    current_section = result

    with open(toml_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith('#'):
                continue

            if line.startswith('[') and line.endswith(']'):
                section_name = line[1:-1].strip()
                section_parts = section_name.split('.')

                current_section = result
                for part in section_parts:
                    if part not in current_section:
                        current_section[part] = {}
                    current_section = current_section[part]
                continue

            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                elif value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                elif value.startswith('{') and value.endswith('}'):
                    value = parse_simple_dict(value)

                current_section[key] = value

    return result


def parse_simple_dict(dict_str: str) -> Dict:
    """Parse a simple dictionary string like { path = "../aion-shared", develop = true }"""
    result = {}
    dict_str = dict_str.strip('{}').strip()

    if not dict_str:
        return result

    parts = []
    current_part = ""
    paren_count = 0

    for char in dict_str:
        if char in '([{':
            paren_count += 1
        elif char in ')]}':
            paren_count -= 1
        elif char == ',' and paren_count == 0:
            parts.append(current_part.strip())
            current_part = ""
            continue
        current_part += char

    if current_part.strip():
        parts.append(current_part.strip())

    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip()

            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            elif value.lower() == 'true':
                value = True
            elif value.lower() == 'false':
                value = False

            result[key] = value

    return result


def get_dependencies(pyproject_data: Dict) -> Dict[str, Union[str, Dict]]:
    """Extract dependencies from pyproject.toml data."""
    dependencies = {}

    deps = pyproject_data.get('tool', {}).get('poetry', {}).get('dependencies', {})
    dependencies.update(deps)

    dev_groups = pyproject_data.get('tool', {}).get('poetry', {}).get('group', {})
    for group_name, group_data in dev_groups.items():
        if 'dependencies' in group_data:
            dependencies.update(group_data['dependencies'])

    return dependencies


def find_libs_packages(libs_dir: Path) -> Set[str]:
    """Find all package names in libs/ directory."""
    packages = set()

    if not libs_dir.exists():
        return packages

    for item in libs_dir.iterdir():
        if item.is_dir() and (item / 'pyproject.toml').exists():
            pyproject_data = load_pyproject(item / 'pyproject.toml')
            package_name = pyproject_data.get('tool', {}).get('poetry', {}).get('name', '')
            if package_name:
                packages.add(package_name)

    return packages


def check_poetry_available(current_dir: Path) -> bool:
    """Check if Poetry is available and current directory has pyproject.toml."""
    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            return False

        pyproject_path = current_dir / 'pyproject.toml'
        return pyproject_path.exists()
    except FileNotFoundError:
        return False


def uninstall_package(package_name: str, package_dir: Path) -> bool:
    """Uninstall a package using poetry run pip uninstall."""
    try:
        cmd = ['poetry', 'run', 'pip', 'uninstall', '-y', package_name]
        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def restore_original_dependencies(package_dir: Path) -> bool:
    """Restore original dependencies using poetry install."""
    try:
        cmd = ['poetry', 'install']
        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)

        if result.returncode == 0:
            return True
        else:
            print(f"❌ Failed to restore dependencies: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error running poetry install: {e}")
        return False


def process_package(package_dir: Path, libs_packages: Set[str]) -> int:
    """Process a single package and restore its original dependencies."""
    pyproject_path = package_dir / 'pyproject.toml'
    if not pyproject_path.exists():
        return 0

    pyproject_data = load_pyproject(pyproject_path)
    dependencies = get_dependencies(pyproject_data)

    # Find local dependencies that might be installed in editable mode
    local_deps = []
    for dep_name, dep_config in dependencies.items():
        if dep_name in libs_packages:
            # Check if it's a local path dependency
            if isinstance(dep_config, dict) and 'path' in dep_config:
                local_deps.append(dep_name)

    if not local_deps:
        return 0

    if not check_poetry_available(package_dir):
        print(f"❌ Poetry not available for {package_dir.name}")
        return 0

    print(f"\n📦 {package_dir.name}: syncing environment with pyproject.toml")

    # Restore original dependencies with poetry install --sync
    if restore_original_dependencies(package_dir):
        print(f"✅ Environment synced (restored {len(local_deps)} dependencies)")
        return len(local_deps)
    else:
        return 0


def main():
    """Main function to process all packages in libs/ directory."""
    current_dir = Path.cwd()
    libs_dir = current_dir / 'libs'

    if not libs_dir.exists():
        print("❌ libs/ directory not found")
        return 1

    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            print("❌ Poetry is not available")
            return 1
    except FileNotFoundError:
        print("❌ Poetry is not available")
        return 1

    libs_packages = find_libs_packages(libs_dir)

    if not libs_packages:
        print("❌ No packages found in libs/")
        return 1

    print(f"Found {len(libs_packages)} packages: {', '.join(sorted(libs_packages))}")

    total_restored = 0
    for package_dir in sorted(libs_dir.iterdir()):
        if package_dir.is_dir():
            total_restored += process_package(package_dir, libs_packages)

    print(f"\n🎉 Restored {total_restored} local dependencies to original versions")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
