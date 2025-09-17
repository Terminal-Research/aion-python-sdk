from typing import Literal, Optional

from aion.shared.aion_config import AgentConfig
from pydantic import Field
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    """Application configuration settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO"
    )

    agent_id: Optional[str] = None
    agent_config: Optional[AgentConfig] = None

    @property
    def url(self) -> str:
        """Application URL."""
        return f"http://0.0.0.0:{self.agent_config.port}"

    def set_agent_config(self, agent_id: str, agent_config: AgentConfig) -> None:
        """Update agent configuration."""
        self.agent_id = agent_id
        self.agent_config = agent_config


app_settings = AppSettings()
