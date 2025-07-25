from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Application configuration settings.

    This model defines the core configuration parameters for the Aion server
    application.
    """

    host: str = Field(
        default="localhost",
        description="Server hostname or IP address to bind to. Use '0.0.0.0' to bind to all interfaces.")

    port: int = Field(
        default=10000,
        description="TCP port number for the server to listen on. Must be between 1 and 65535.",
        ge=1,
        le=65535)
