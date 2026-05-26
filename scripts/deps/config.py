from collections import deque
from pathlib import Path

from typing import List

ROOT_DIR = Path(__file__).parent.parent.parent
LIBS_DIR = ROOT_DIR / "libs"

# Unified configuration: package name -> list of direct local dependencies
# Note: Only direct dependencies need to be specified. Transitive dependencies
# are resolved automatically using resolve_dependencies() function.
PACKAGES = {
    "aion-sdk": [
        "aion-server",
        "aion-authoring-langgraph",
        "aion-server-langgraph",
        "aion-authoring-adk",
        "aion-server-adk",
    ],
    "aion-server": [
        "aion-core",
        "aion-api-client",
        "aion-db",
    ],
    "aion-server-langgraph": [
        "aion-core",
        "aion-server",
        "aion-authoring-langgraph",
        "aion-db",
    ],
    "aion-server-adk": [
        "aion-authoring-adk",
        "aion-server",
    ],
    "aion-authoring-langgraph": [
        "aion-core",
        "aion-api-client",
        "aion-mcp",
    ],
    "aion-authoring-adk": [
        "aion-api-client",
        "aion-mcp",
    ],
    "aion-api-client": [
        "aion-core",
    ],
    "aion-mcp": [
        "aion-api-client",
    ],
    "aion-db": [
        "aion-core",
    ],
    "aion-core": [],
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
