from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO")


app_settings = AppSettings()
