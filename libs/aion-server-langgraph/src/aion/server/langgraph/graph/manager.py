from __future__ import annotations

import os
import logging
import importlib
import importlib.util
import inspect
import yaml
from pathlib import Path
from types import ModuleType
from typing import Dict, Any

from langgraph.checkpoint.memory import InMemorySaver

from aion.server.utils.metaclasses import Singleton

logger = logging.getLogger(__name__)

# LangGraph is optional in this environment. Define minimal stubs if the package
# is not installed so type checks and isinstance comparisons do not fail at
# runtime when LangGraph is available.
try:  # pragma: no cover - optional dependency
    from langgraph.graph import Graph
    from langgraph.pregel import Pregel
except Exception:  # pragma: no cover - local testing without dependency
    from .langgraph_interfaces import Graph, Pregel


class GraphManager(metaclass=Singleton):
    """Manages graph registration and loading."""

    def __init__(self):
        self.graphs: Dict[str, Any] = {}

    def set_graph(self, graph_id: str, graph: Any) -> None:
        """Register a graph object by ID.

        Args:
            graph_id: Unique identifier for the graph.
            graph: Graph object to register.
        """
        if self.graphs.get(graph_id):
            logger.info(f"Updating graph \"{graph_id}\"")
        else:
            logger.info(f"Registering graph \"{graph_id}\"")

        self.graphs[graph_id] = graph

    def get_graph(self, graph_id: str) -> Any:
        """Return a registered graph.

        Args:
            graph_id: Unique identifier of the graph to retrieve.

        Returns:
            The registered graph object.
        """
        return self.graphs.get(graph_id)

    def _import_module(self, module_str: str, base_dir: Path) -> ModuleType:
        """Import a module from a dotted path or a file path.

        Args:
            module_str: Module path string (dotted notation or file path).
            base_dir: Base directory for resolving relative paths.

        Returns:
            Imported module object.

        Raises:
            FileNotFoundError: If the module file is not found.
            ValueError: If the module cannot be loaded.
        """
        if module_str.endswith(".py") or "/" in module_str or module_str.startswith("."):
            path = (base_dir / module_str).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Graph module not found: {path}")

            mod_name = path.stem + "_graph"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load module from path: {path}")

            logger.debug("Importing module from %s", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[arg-type]
        else:
            logger.debug("Importing module '%s'", module_str)
            module = importlib.import_module(module_str)
        return module

    def _load_graph(self, import_str: str, base_dir: Path) -> Any:
        """Load a graph object from an import string.

        Args:
            import_str: Import string in format 'module:attribute' or just 'module'.
            base_dir: Base directory for resolving relative module paths.

        Returns:
            Loaded and potentially compiled graph object.

        Raises:
            ValueError: If the graph cannot be found or loaded.
        """
        module_part, _, var_part = import_str.partition(":")
        module = self._import_module(module_part, base_dir)
        logger.debug("Loaded module '%s' for graph", module_part)

        if var_part:
            if var_part not in module.__dict__:
                raise ValueError(
                    f"Could not find graph '{var_part}' in module '{module_part}'"
                )
            obj = module.__dict__[var_part]
            logger.debug("Found attribute '%s' in module '%s'", var_part, module_part)
        else:
            obj = None
            for _, member in inspect.getmembers(module):
                if isinstance(member, Pregel):
                    obj = member
                    logger.debug(
                        "Discovered Pregel instance '%s' in module '%s'", member, module_part
                    )
                    break
            if obj is None:
                for _, member in inspect.getmembers(module):
                    if isinstance(member, Graph):
                        obj = member
                        logger.debug(
                            "Discovered Graph instance '%s' in module '%s'", member, module_part
                        )
                        break
            if obj is None:
                raise ValueError(f"No graph found in module '{module_part}'")

        if callable(obj) and not isinstance(obj, Pregel | Graph):
            # Factory function with no arguments
            if len(inspect.signature(obj).parameters) != 0:
                raise ValueError(
                    f"Graph factory '{var_part}' in module '{module_part}' must take no arguments"
                )
            logger.debug("Calling factory '%s' for graph", var_part or module_part)
            obj = obj()

        return obj

    def _load_simple_yaml(self, path: Path) -> Dict[str, Any]:
        """Load a YAML file using PyYAML.

        Args:
            path: Location of the YAML file.

        Returns:
            Parsed mapping.
        """
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a mapping at the root")

        return data

    def _compile_graph(self, graph_id: str) -> None:
        """Compile a single graph by its ID.

        Args:
            graph_id (str): The unique identifier of the graph to compile.
        """
        graph = self.get_graph(graph_id)
        if not graph:
            logger.warning(f"Graph \"{graph_id}\" was not found for compiling.")
            return

        if hasattr(graph, "compile") and callable(getattr(graph, "compile")):
            logger.info(f"Compiling graph \"{graph_id}\"")
            compiled_graph = graph.compile(checkpointer=self._get_checkpointer_for_graph(graph_id))
            self.set_graph(graph_id=graph_id, graph=compiled_graph)

    def _get_checkpointer_for_graph(self, graph_id: str):
        graph = self.get_graph(graph_id)
        if not graph:
            logger.warning(f"Graph \"{graph_id}\" was not found for adding checkpointer.")

        return InMemorySaver()

    def compile_graphs(self) -> None:
        """Compile all graphs in the collection.

        Iterates through all available graphs and compiles each one by calling
        the private _compile_graph method. This ensures all graphs are properly
        compiled and ready for use.
        """
        for graph_id in self.graphs.keys():
            self._compile_graph(graph_id)

    def initialize_graphs(self, config_path: str | Path = "aion.yaml") -> None:
        """Load and register graphs declared in ``aion.yaml``.

        Args:
            config_path: Path to the configuration file. Defaults to ``aion.yaml``
                in the current working directory.
        """
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(os.getcwd()) / path

        logger.info("Loading graphs from %s", path)
        config = self._load_simple_yaml(path)

        graphs_cfg = config.get("aion", {}).get("graph", {})
        if not graphs_cfg:
            logger.warning("No graphs configured in %s", path)

        base_dir = path.parent
        for graph_id, import_str in graphs_cfg.items():
            logger.info("Importing graph '%s' from '%s'", graph_id, import_str)
            graph_obj = self._load_graph(import_str, base_dir)
            self.set_graph(graph_id=graph_id, graph=graph_obj)

        self.compile_graphs()
        logger.info("Initialized %d graphs", len(graphs_cfg))

    def has_active_graph(self) -> bool:
        """Check if one of initialized graphs is active / loaded."""
        return any(self.graphs.values())




graph_manager = GraphManager()
