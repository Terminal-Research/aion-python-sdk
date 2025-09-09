from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    """Application configuration settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO"
    )

    host: Optional[str] = Field(
        default=None,
        description="Host address where the application will run."
    )
    port: Optional[int] = Field(
        default=None,
        description="Port number on which the application will listen."
    )

    @property
    def url(self) -> str:
        """Application URL."""
        return f"http://{self.host}:{self.port}"

    def update_serve_settings(self, host: str, port: int) -> None:
        """Update host and port settings in the current instance."""
        self.host = host
        self.port = port


app_settings = AppSettings()
