from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"

# Unified configuration: package name -> list of direct local dependencies
# Note: Only direct dependencies need to be specified. Transitive dependencies
# are resolved automatically using resolve_dependencies() function.
PACKAGES = {
    "aion-cli": ["aion-server"],
    "aion-server": ["aion-api-client", "aion-shared"],
    "aion-api-client": ["aion-shared"],
    "aion-mcp": ["aion-shared"],
    "aion-shared": [],
}


def resolve_dependencies(package_name: str, visited: set = None, resolved: List[str] = None) -> List[str]:
    """
    Recursively resolve all transitive dependencies for a package in reverse topological order.

    This installs from higher-level to lower-level dependencies.
    For example, if aion-cli depends on aion-server, which depends on
    aion-api-client and aion-shared, the installation order will be:
    1. aion-server
    2. aion-api-client
    3. aion-shared

    Args:
        package_name: The package to resolve dependencies for
        visited: Set of packages currently being visited (for cycle detection)
        resolved: List of resolved dependencies in order

    Returns:
        List of all dependencies in installation order (high-level to low-level)

    Raises:
        ValueError: If a circular dependency is detected
    """
    if visited is None:
        visited = set()
    if resolved is None:
        resolved = []

    if package_name in visited:
        # Circular dependency detected
        raise ValueError(f"Circular dependency detected: {package_name}")

    if package_name in resolved:
        # Already resolved
        return resolved

    visited.add(package_name)

    # Get direct dependencies
    direct_deps = PACKAGES.get(package_name, [])

    # Add this package first (before its dependencies for reverse order)
    if package_name not in resolved:
        resolved.append(package_name)

    # Recursively resolve each dependency
    for dep in direct_deps:
        resolve_dependencies(dep, visited, resolved)

    visited.remove(package_name)

    return resolved
