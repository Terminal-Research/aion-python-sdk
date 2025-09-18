"""Configuration for the Aion API client."""

from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Aion API client settings."""

    client_id: Optional[str] = Field(
        # ...,
        default=None,
        alias="AION_CLIENT_ID",
        description="Client ID for API authentication"
    )

    client_secret: Optional[str] = Field(
        # ...,
        default=None,
        alias="AION_CLIENT_SECRET",
        description="Client secret for API authentication"
    )

    api_host: str = Field(
        default="https://api.aion.to",
        alias="AION_API_HOST",
        description="API host URL"
    )

    api_keep_alive: int = Field(
        default=60,
        alias="AION_API_KEEP_ALIVE",
        description="Keep alive interval in seconds"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @computed_field
    @property
    def scheme(self) -> str:
        """Get the URL scheme (http/https)."""
        return urlparse(self.api_host).scheme

    @computed_field
    @property
    def hostname(self) -> str:
        """Get the hostname from the API URL."""
        return urlparse(self.api_host).hostname

    @computed_field
    @property
    def port(self) -> int:
        """Get the port number from the API URL."""
        parsed = urlparse(self.api_host)
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, v):
        """Validate API host URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("API host must start with http:// or https://")
        return v.rstrip("/")



# Initialize settings
try:
    api_settings = ApiSettings()
except Exception as e:
    print(f"Error loading configuration: {e}")
    print("Please check your .env file and ensure all required variables are set.")
    raise
