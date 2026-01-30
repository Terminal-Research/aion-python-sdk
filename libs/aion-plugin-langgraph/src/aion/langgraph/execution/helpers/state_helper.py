from typing import Any


class StateHelper:
    """Helper for checking graph state schema properties."""

    @staticmethod
    def has_property(compiled_graph: Any, property_name: str) -> bool:
        """Check if graph state schema includes a specific property.

        Args:
            compiled_graph: The compiled LangGraph graph
            property_name: Name of the state property to check

        Returns:
            True if the property exists in the graph state schema
        """
        if not hasattr(compiled_graph, 'channels'):
            return False

        return property_name in compiled_graph.channels
