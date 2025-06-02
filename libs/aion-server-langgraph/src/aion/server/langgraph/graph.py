"""Utilities for registering LangGraph graphs from configuration."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from typing import Tuple
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

# LangGraph is optional in this environment. Define minimal stubs if the package
# is not installed so type checks and isinstance comparisons do not fail at
# runtime when LangGraph is available.
try:  # pragma: no cover - optional dependency
    from langgraph.graph import Graph
    from langgraph.pregel import Pregel
except Exception:  # pragma: no cover - local testing without dependency
    class Graph:  # type: ignore
        """Fallback Graph stub used when langgraph is unavailable."""

        def compile(self) -> Any:  # pragma: no cover - simple stub
            return self

    class Pregel:  # type: ignore
        """Fallback Pregel stub used when langgraph is unavailable."""

        pass


# Registry of loaded graphs keyed by their ID
GRAPHS: Dict[str, Any] = {}


def register_graph(graph_id: str, graph: Any) -> None:
    """Register a graph object by ID."""
    logger.info("Registering graph '%s'", graph_id)
    GRAPHS[graph_id] = graph


def get_graph(graph_id: str) -> Any:
    """Return a registered graph."""
    return GRAPHS[graph_id]


def _import_module(module_str: str, base_dir: Path) -> ModuleType:
    """Import a module from a dotted path or a file path."""
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


def _load_graph(import_str: str, base_dir: Path) -> Any:
    """Load a graph object from an import string."""
    module_part, _, var_part = import_str.partition(":")
    module = _import_module(module_part, base_dir)
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

    if hasattr(obj, "compile") and callable(getattr(obj, "compile")):
        logger.debug("Compiling Graph instance from module '%s'", module_part)
        obj = obj.compile()

    return obj


def _load_simple_yaml(path: Path) -> Dict[str, Any]:
    """Load a minimal YAML file.

    The parser only understands indentation based mappings with string values,
    which is sufficient for the ``aion.yaml`` configuration file.

    Args:
        path: Location of the YAML file.

    Returns:
        Parsed mapping.
    """

    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()

    root: Dict[str, Any] = {}
    stack: list[Tuple[int, Dict[str, Any]]] = [(0, root)]

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        key, _, value = line.lstrip().partition(":")
        key = key.strip()
        value = value.strip()

        while stack and indent < stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]

        if value == "":
            new_dict: Dict[str, Any] = {}
            parent[key] = new_dict
            stack.append((indent + 2, new_dict))
        else:
            if value.startswith("\"") and value.endswith("\""):
                value = value[1:-1]
            parent[key] = value

    return root


def initialize_graphs(config_path: str | Path = "aion.yaml") -> None:
    """Load and register graphs declared in ``aion.yaml``.

    Args:
        config_path: Path to the configuration file. Defaults to ``aion.yaml``
            in the current working directory.
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    logger.info("Loading graphs from %s", path)
    config = _load_simple_yaml(path)

    graphs_cfg = config.get("aion", {}).get("graph", {})
    if not graphs_cfg:
        logger.warning("No graphs configured in %s", path)
    base_dir = path.parent
    for graph_id, import_str in graphs_cfg.items():
        logger.info("Importing graph '%s' from '%s'", graph_id, import_str)
        graph_obj = _load_graph(import_str, base_dir)
        register_graph(graph_id, graph_obj)
    logger.info("Initialized %d graphs", len(graphs_cfg))

