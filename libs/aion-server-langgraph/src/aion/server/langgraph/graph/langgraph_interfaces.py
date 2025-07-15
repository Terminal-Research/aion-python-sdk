from typing import Any

__all__ = [
    "Graph",
    "Pregel",
]


class Graph:  # type: ignore
    """Fallback Graph stub used when langgraph is unavailable."""

    def compile(self) -> Any:  # pragma: no cover - simple stub
        return self


class Pregel:  # type: ignore
    """Fallback Pregel stub used when langgraph is unavailable."""

    pass
