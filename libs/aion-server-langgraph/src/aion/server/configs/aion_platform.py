from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AionPlatformSettings(BaseSettings):
    """
    Configuration settings for Aion platform integration.

    Settings can be overridden using environment variables with the
    corresponding alias names (e.g., AION_DOCS_URL).
    """

    docs_url: str = Field(
        default="https://docs.aion.to/",
        description="Url to the documentation of Aion API.",
        alias="AION_DOCS_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

aion_platform_settings = AionPlatformSettings()
