#!/usr/bin/env python3
"""
Script to install local dependencies from libs/ directory in editable mode using Poetry pip.
Analyzes pyproject.toml files and installs internal packages with 'poetry run pip install -e'
without modifying pyproject.toml or poetry.lock files.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

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
    section_path = []

    with open(toml_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()

            if not line or line.startswith('#'):
                continue

            if line.startswith('[') and line.endswith(']'):
                section_name = line[1:-1].strip()
                section_parts = section_name.split('.')

                current_section = result
                section_path = []

                for part in section_parts:
                    section_path.append(part)
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


def get_package_name(pyproject_data: Dict) -> str:
    """Extract package name from pyproject.toml data."""
    return pyproject_data.get('tool', {}).get('poetry', {}).get('name', '')


def get_dependencies(pyproject_data: Dict) -> Dict[str, str]:
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
            package_name = get_package_name(pyproject_data)
            if package_name:
                packages.add(package_name)

    return packages


def get_package_path(package_name: str, libs_dir: Path) -> Path:
    """Get the path to a package in libs/ directory."""
    package_dir = libs_dir / package_name
    if package_dir.exists() and (package_dir / 'pyproject.toml').exists():
        return package_dir

    for item in libs_dir.iterdir():
        if item.is_dir() and (item / 'pyproject.toml').exists():
            pyproject_data = load_pyproject(item / 'pyproject.toml')
            if get_package_name(pyproject_data) == package_name:
                return item

    raise FileNotFoundError(f"Package {package_name} not found in libs/")


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


def install_package_editable(package_path: Path, current_dir: Path) -> bool:
    """Install package in editable mode using poetry run pip install -e."""
    try:
        relative_path = os.path.relpath(package_path, current_dir)
        cmd = ['poetry', 'run', 'pip', 'install', '-e', relative_path]

        result = subprocess.run(cmd, cwd=current_dir, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✅ Installed {package_path.name}")
            return True
        else:
            print(f"❌ Failed to install {package_path.name}: {result.stderr}")
            return False

    except Exception as e:
        print(f"❌ Error installing {package_path.name}: {e}")
        return False


def process_package(package_dir: Path, libs_packages: Set[str], libs_dir: Path) -> int:
    """Process a single package and install its local dependencies."""
    pyproject_path = package_dir / 'pyproject.toml'
    if not pyproject_path.exists():
        return 0

    pyproject_data = load_pyproject(pyproject_path)
    dependencies = get_dependencies(pyproject_data)

    local_deps = [dep for dep in dependencies.keys() if dep in libs_packages]

    if not local_deps:
        return 0

    if not check_poetry_available(package_dir):
        print(f"❌ Poetry not available for {package_dir.name}")
        return 0

    print(f"\n📦 {package_dir.name}: installing {', '.join(local_deps)}")

    installed_count = 0
    for dep_name in local_deps:
        try:
            dep_path = get_package_path(dep_name, libs_dir)
            if install_package_editable(dep_path, package_dir):
                installed_count += 1
        except FileNotFoundError:
            print(f"❌ {dep_name} not found")
        except Exception as e:
            print(f"❌ Error with {dep_name}: {e}")

    return installed_count


def main():
    """Main function to process all packages in libs/ directory."""
    current_dir = Path.cwd()
    libs_dir = current_dir / 'libs'
    print("libs_dir", libs_dir)

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

    total_installed = 0
    for package_dir in sorted(libs_dir.iterdir()):
        if package_dir.is_dir():
            total_installed += process_package(package_dir, libs_packages, libs_dir)

    print(f"\n🎉 Installed {total_installed} local dependencies")
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
