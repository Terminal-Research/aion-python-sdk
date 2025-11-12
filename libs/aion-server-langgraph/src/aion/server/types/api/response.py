from typing import Dict

from aion.shared.aion_config.models import ConfigurationField
from pydantic import BaseModel, Field


class ConfigurationFileResponse(BaseModel):
    """Response model for GET /.well-known/configuration.json

    Represents the agent configuration file containing protocol version
    and agent-specific configuration details.
    """
    protocolVersion: str = Field(
        ...,
        description="A2A protocol version"
    )
    configuration: Dict[str, ConfigurationField] = Field(
        ...,
        description="Agent configuration details"
    )