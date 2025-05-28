"""
Configuration module for the AION Agent API server.

This module handles loading environment variables and provides configuration
settings for the API server.
"""

import os
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()


class ServerConfig(BaseModel):
    """Configuration settings for the API server."""
    
    # Server settings
    host: str = Field(
        default=os.getenv("HOST", "0.0.0.0"),
        description="Host address to bind to"
    )
    port: int = Field(
        default=int(os.getenv("PORT", "8000")),
        description="Port to listen on"
    )
    debug: bool = Field(
        default=os.getenv("DEBUG", "False").lower() == "true",
        description="Whether to enable debug mode"
    )
    
    # LangGraph settings
    graph_checkpoint_dir: Optional[str] = Field(
        default=os.getenv("GRAPH_CHECKPOINT_DIR"),
        description="Directory for storing graph checkpoints"
    )
    
    # OpenAI API settings
    openai_api_key: str = Field(
        default=os.getenv("OPENAI_API_KEY", ""),
        description="OpenAI API key"
    )
    
    # Logging settings
    log_level: str = Field(
        default=os.getenv("LOG_LEVEL", "INFO"),
        description="Logging level"
    )
    
    class Config:
        """Pydantic model configuration."""
        validate_assignment = True
    
    def as_dict(self) -> Dict[str, Any]:
        """Return the configuration as a dictionary."""
        return self.model_dump()


# Create a singleton instance of the configuration
server_config = ServerConfig()


def get_config() -> ServerConfig:
    """
    Get the API server configuration.
    
    Returns:
        ServerConfig: The server configuration instance
    """
    return server_config
