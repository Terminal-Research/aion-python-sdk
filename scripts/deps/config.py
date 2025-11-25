from collections import deque
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"

# Unified configuration: package name -> list of direct local dependencies
# Note: Only direct dependencies need to be specified. Transitive dependencies
# are resolved automatically using resolve_dependencies() function.
PACKAGES = {
    "aion-cli": ["aion-server"],
    "aion-server": ["aion-adapter-langgraph", "aion-api-client"],
    "aion-adapter-langgraph": ["aion-shared"],
    "aion-api-client": ["aion-shared"],
    "aion-mcp": ["aion-shared"],
    "aion-shared": [],
}


def resolve_dependencies(package_name: str) -> List[str]:
    """
    Resolve all transitive dependencies for a package using level-order traversal (BFS).

    Dependencies are resolved level by level, ensuring higher-level packages
    are installed before their dependencies.

    Args:
        package_name: The package to resolve dependencies for

    Returns:
        List of all packages in installation order (current package, then dependencies level by level)

    Raises:
        ValueError: If a circular dependency is detected
    """
    result = []
    seen = set()
    queue = deque([package_name])
    in_progress = set()

    while queue:
        current = queue.popleft()

        # Skip if already processed
        if current in seen:
            continue

        # Check for circular dependency
        if current in in_progress:
            raise ValueError(f"Circular dependency detected: {current}")

        in_progress.add(current)
        seen.add(current)
        result.append(current)

        # Add direct dependencies to queue
        direct_deps = PACKAGES.get(current, [])
        for dep in direct_deps:
            if dep not in seen:
                queue.append(dep)

        in_progress.remove(current)

    return result
