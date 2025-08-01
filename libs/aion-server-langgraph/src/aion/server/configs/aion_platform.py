from pydantic import Field
from pydantic_settings import BaseSettings


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

aion_platform_settings = AionPlatformSettings()
