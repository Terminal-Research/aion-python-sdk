from a2a.types import AgentCard

from .collectors import AgentCardConfigurationCollector


class AionAgentCard(AgentCard):
    """
    Extended AgentCard with configuration management capabilities.

    This class extends the base AgentCard to include automatic configuration
    processing through the AgentCardConfigurationCollector. It standardizes
    configuration data into a consistent format suitable for agent management.

    Attributes:
        configuration (dict[str, dict]): Dictionary containing processed configuration
                                       data where keys are field names and values are
                                       configuration dictionaries with validation rules
                                       and metadata.
    """
    configuration: dict[str, dict] = {}

    def __init__(self, *args, configuration = None, **kwargs):
        """
        Initialize AionAgentCard with configuration processing.

        Creates an AionAgentCard instance by calling the parent AgentCard constructor
        and processing the provided configuration data through AgentCardConfigurationCollector.
        """
        super().__init__(*args, **kwargs)
        self.configuration = AgentCardConfigurationCollector(configuration or {}).collect()


__all__ = [
    "AionAgentCard",
]
