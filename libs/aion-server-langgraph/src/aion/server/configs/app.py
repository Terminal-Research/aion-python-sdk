from typing import Literal, Optional

from aion.shared.aion_config import AgentConfig
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application configuration settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO"
    )

    agent_id: Optional[str] = None
    agent_config: Optional[AgentConfig] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def url(self) -> str:
        """Application URL."""
        if not self.agent_config:
            raise AttributeError("agent_config must be set for AppSettings")

        return f"http://0.0.0.0:{self.agent_config.port}"

    def set_agent_config(self, agent_id: str, agent_config: AgentConfig) -> None:
        """Update agent configuration."""
        self.agent_id = agent_id
        self.agent_config = agent_config


app_settings = AppSettings()
