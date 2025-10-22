"""Module loader for loading Python modules from various path formats.

This module provides utilities for loading Python modules from different
path formats (dotted paths, file paths, etc.) used in agent configurations.
It also provides discovery functionality to find specific objects within modules.
"""

import importlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from aion.shared.logging import get_logger

logger = get_logger()


class ModuleLoader:
    """Loader for Python modules from configuration paths.

    This class handles loading modules from various path formats:
    - Dotted module paths: "my.package.module"
    - Relative file paths: "agents/my_agent.py"
    - Absolute file paths: "/path/to/agent.py"
    - Paths starting with dot: "./relative/path.py"
    """

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize module loader.

        Args:
            base_path: Base directory for resolving relative file paths.
                      Defaults to current working directory.
        """
        self.base_path = base_path or Path.cwd()
        logger.debug(f"ModuleLoader initialized with base_path={self.base_path}")

    def load(self, path: str) -> ModuleType:
        """Load a Python module from a path string.

        Args:
            path: Module path in one of the supported formats:
                  - Dotted path: "my.module.path"
                  - File path: "path/to/module.py" or "./module.py"
                  - Absolute path: "/absolute/path/module.py"

        Returns:
            ModuleType: Loaded Python module

        Raises:
            FileNotFoundError: If file path doesn't exist
            ValueError: If module cannot be loaded
            ImportError: If dotted path import fails
        """
        if self._is_file_path(path):
            return self._load_from_file(path)
        else:
            return self._load_from_dotted_path(path)

    @staticmethod
    def _is_file_path(path: str) -> bool:
        """Check if path is a file path (vs dotted module path).

        Args:
            path: Path string to check

        Returns:
            bool: True if it looks like a file path
        """
        return (
                path.endswith(".py") or
                "/" in path or
                "\\" in path or
                path.startswith(".")
        )

    def _load_from_file(self, file_path: str) -> ModuleType:
        """Load module from a file path.

        Args:
            file_path: Relative or absolute file path to .py file

        Returns:
            ModuleType: Loaded module

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file cannot be loaded as a module
        """
        # Resolve path relative to base_path
        if Path(file_path).is_absolute():
            module_path = Path(file_path)
        else:
            module_path = (self.base_path / file_path).resolve()

        if not module_path.exists():
            raise FileNotFoundError(
                f"Module file not found: {module_path}\n"
                f"(resolved from '{file_path}' with base_path={self.base_path})"
            )

        # Create unique module name from file path
        mod_name = module_path.stem + "_module"

        # Load module using importlib
        spec = importlib.util.spec_from_file_location(mod_name, module_path)
        if spec is None or spec.loader is None:
            raise ValueError(
                f"Could not create module spec from file: {module_path}\n"
                f"File may not be a valid Python module."
            )

        logger.debug(f"Loading module from file: {module_path}")
        module = importlib.util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise ValueError(
                f"Failed to execute module from {module_path}: {e}"
            ) from e

        logger.debug(f"Successfully loaded module '{mod_name}' from {module_path}")
        return module

    def _load_from_dotted_path(self, dotted_path: str) -> ModuleType:
        """Load module from a dotted Python path.

        Args:
            dotted_path: Dotted module path (e.g., "my.package.module")

        Returns:
            ModuleType: Loaded module

        Raises:
            ImportError: If module cannot be imported
        """
        logger.debug(f"Importing module from dotted path: {dotted_path}")

        try:
            module = importlib.import_module(dotted_path)
        except ImportError as e:
            raise ImportError(
                f"Could not import module '{dotted_path}': {e}\n"
                f"Make sure the module is in PYTHONPATH or installed."
            ) from e

        logger.debug(f"Successfully imported module: {dotted_path}")
        return module

    def load_from_config_path(self, config_path: str) -> tuple[ModuleType, Optional[str]]:
        """Load module from agent config path format.

        Agent config paths can specify a module and optionally an item within it:
        - "module.path" → load module, no specific item
        - "module.path:ItemName" → load module, extract "ItemName"
        - "path/to/file.py:ItemName" → load file, extract "ItemName"

        Args:
            config_path: Configuration path string

        Returns:
            tuple: (loaded_module, item_name or None)
        """
        # Parse module and item name
        module_part, _, item_part = config_path.partition(":")

        # Load the module
        module = self.load(module_part)

        # Return module and optional item name
        item_name = item_part if item_part else None

        return module, item_name

    @staticmethod
    def discover_object(
            module: ModuleType,
            supported_types: list[type],
            supported_type_names: set[str],
            item_name: Optional[str] = None,
    ) -> Any:
        """Discover an object in a module by type.

        Searches for objects (classes, instances, callables) matching the
        supported types. If item_name is provided, looks for that specific item.

        Args:
            module: Module to search in
            supported_types: List of types to match (using isinstance/issubclass)
            supported_type_names: Set of class names to match by string
            item_name: Optional specific item name to look for

        Returns:
            Any: Found object

        Raises:
            ValueError: If no matching object found

        Priority order (when item_name is None):
        1. Classes that are subclasses of supported_types
        2. Instances of supported_types
        3. Callables that might return supported types
        """
        if item_name:
            # Explicit item name provided - get it directly
            if not hasattr(module, item_name):
                raise ValueError(
                    f"Item '{item_name}' not found in module '{module.__name__}'"
                )
            obj = getattr(module, item_name)
            logger.debug(
                f"Found explicit item '{item_name}' of type '{type(obj).__name__}'"
            )
            return obj

        # Auto-discovery: try to find objects matching supported types
        found_objects = []

        # 1. Search for classes (subclasses of supported types)
        for name, member in inspect.getmembers(module, inspect.isclass):
            # Skip if not defined in this module
            if member.__module__ != module.__name__:
                continue

            # Check if it's a subclass of any supported type
            for supported_type in supported_types:
                try:
                    if issubclass(member, supported_type) and member is not supported_type:
                        logger.debug(
                            f"Found class '{name}' (subclass of {supported_type.__name__})"
                        )
                        found_objects.append(("class", member, name))
                        break
                except TypeError:
                    # issubclass can raise TypeError for some objects
                    continue

        # 2. Search for instances of supported types
        for name, member in inspect.getmembers(module):
            # Skip private members and already found classes
            if name.startswith("_"):
                continue

            # Check by instance type
            for supported_type in supported_types:
                if isinstance(member, supported_type):
                    logger.debug(
                        f"Found instance '{name}' of type '{type(member).__name__}'"
                    )
                    found_objects.append(("instance", member, name))
                    break

            # Check by class name (for types we can't import)
            class_name = type(member).__name__
            if class_name in supported_type_names:
                logger.debug(
                    f"Found object '{name}' with matching class name '{class_name}'"
                )
                found_objects.append(("named_type", member, name))

        # 3. Search for callables (functions that might return supported types)
        for name, member in inspect.getmembers(module, inspect.isfunction):
            # Skip private functions
            if name.startswith("_"):
                continue

            # Only add if we haven't found anything better
            if not found_objects:
                logger.debug(f"Found callable '{name}'")
                found_objects.append(("callable", member, name))

        # Return the first found object (priority: class > instance > callable)
        if not found_objects:
            raise ValueError(
                f"No supported object found in module '{module.__name__}'. "
                f"Supported types: {[t.__name__ for t in supported_types]}, "
                f"Supported type names: {supported_type_names}"
            )

        # Sort by priority: class first, then instance, then named_type, then callable
        priority = {"class": 0, "instance": 1, "named_type": 2, "callable": 3}
        found_objects.sort(key=lambda x: priority.get(x[0], 999))

        obj_type, obj, obj_name = found_objects[0]
        logger.info(
            f"Discovered {obj_type} '{obj_name}' in module '{module.__name__}'"
        )
        return obj
