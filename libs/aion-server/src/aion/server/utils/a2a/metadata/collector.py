from dataclasses import dataclass
from typing import Dict, Any, Optional

from aion.shared.types import A2AMetadataKey


@dataclass
class AgentIdentity:
    """Represents agent identity information from metadata"""
    id: str
    user_id: str
    name: str
    at_name: str
    biography: str
    avatar_image_url: Optional[str] = None
    background_image_url: Optional[str] = None


@dataclass
class AgentBehavior:
    """Represents agent behavior configuration from metadata"""
    id: str
    graph_id: str
    version_id: str


@dataclass
class AgentEnvironment:
    """Represents agent environment configuration from metadata"""
    id: str
    name: str
    configuration_variables: Dict[str, str]
    use_long_term_memory: bool
    system_prompt: Optional[str] = None


@dataclass
class DistributionInfo:
    """Represents distribution information from metadata"""
    id: str
    identity: AgentIdentity
    behavior: AgentBehavior
    environment: AgentEnvironment


@dataclass
class A2AMetadata:
    """Represents complete A2A metadata structure"""
    sender_id: str
    network: str
    distribution: DistributionInfo


class A2AMetadataCollector:
    """Collector class for extracting and organizing A2A message metadata from raw data"""

    def __init__(self, metadata: Dict[str, Any]):
        """
        Initialize the metadata collector with raw data

        Args:
            metadata: Raw metadata dictionary
        """
        self._metadata = metadata
        self._normalized_metadata = None

    def get_normalized_metadata(self) -> A2AMetadata:
        """
        Extract and organize metadata from stored raw data

        Returns:
            A2AMetadata: Structured metadata object
        """
        if self._normalized_metadata is not None:
            return self._normalized_metadata

        # Extract basic metadata
        sender_id = self._metadata.get(A2AMetadataKey.SENDER_ID.value)
        network = self._metadata.get(A2AMetadataKey.NETWORK.value)
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})

        # Extract identity information
        identity_data = distribution_data.get("identity", {})
        identity = AgentIdentity(
            id=identity_data.get("id"),
            user_id=identity_data.get("userId"),
            name=identity_data.get("name"),
            at_name=identity_data.get("atName"),
            biography=identity_data.get("biography"),
            avatar_image_url=identity_data.get("avatarImageUrl"),
            background_image_url=identity_data.get("backgroundImageUrl")
        )

        # Extract behavior information
        behavior_data = distribution_data.get("behavior", {})
        behavior = AgentBehavior(
            id=behavior_data.get("id"),
            graph_id=behavior_data.get("graphId"),
            version_id=behavior_data.get("versionId")
        )

        # Extract environment information
        environment_data = distribution_data.get("environment", {})
        environment = AgentEnvironment(
            id=environment_data.get("id"),
            name=environment_data.get("name"),
            configuration_variables=environment_data.get("configurationVariables", {}),
            use_long_term_memory=environment_data.get("useLongTermMemory", False),
            system_prompt=environment_data.get("systemPrompt")
        )

        # Create distribution info
        distribution = DistributionInfo(
            id=distribution_data.get("id"),
            identity=identity,
            behavior=behavior,
            environment=environment
        )

        # Create complete metadata structure
        self._normalized_metadata = A2AMetadata(
            sender_id=sender_id,
            network=network,
            distribution=distribution
        )

        return self._normalized_metadata

    def get_sender_id(self) -> str:
        """Extract sender ID from raw metadata"""
        return self._metadata.get(A2AMetadataKey.SENDER_ID.value)

    def get_network_source(self) -> str:
        """Extract network source from raw metadata"""
        return self._metadata.get(A2AMetadataKey.NETWORK.value)

    def get_agent_identity(self) -> AgentIdentity:
        """Extract agent identity from raw metadata"""
        metadata = self.get_normalized_metadata()
        return metadata.distribution.identity

    def get_graph_id(self) -> str:
        """Extract graph ID for Langgraph operations from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        behavior_data = distribution_data.get("behavior", {})
        return behavior_data.get("graphId")

    def get_environment_id(self) -> str:
        """Extract environment ID for resource operations from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        environment_data = distribution_data.get("environment", {})
        return environment_data.get("id")

    def get_system_prompt(self) -> Optional[str]:
        """Extract system prompt for basic agents from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        environment_data = distribution_data.get("environment", {})
        return environment_data.get("systemPrompt")

    def get_configuration_variables(self) -> Dict[str, str]:
        """Extract configuration variables from environment in raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        environment_data = distribution_data.get("environment", {})
        return environment_data.get("configurationVariables", {})

    def should_use_long_term_memory(self) -> bool:
        """Check if long-term memory should be used from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        environment_data = distribution_data.get("environment", {})
        return environment_data.get("useLongTermMemory", False)

    def get_raw_metadata(self) -> Dict[str, Any]:
        """Get the original metadata dictionary"""
        return self._metadata.copy()

    def get_distribution_id(self) -> str:
        """Extract distribution ID from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        return distribution_data.get("id")

    def get_behavior_version_id(self) -> str:
        """Extract behavior version ID from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        behavior_data = distribution_data.get("behavior", {})
        return behavior_data.get("versionId")

    def get_agent_name(self) -> str:
        """Extract agent name from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        identity_data = distribution_data.get("identity", {})
        return identity_data.get("name")

    def get_agent_at_name(self) -> str:
        """Extract agent @name from raw metadata"""
        distribution_data = self._metadata.get(A2AMetadataKey.DISTRIBUTION.value, {})
        identity_data = distribution_data.get("identity", {})
        return identity_data.get("atName")
