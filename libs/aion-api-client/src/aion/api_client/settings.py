"""Configuration for the Aion API client."""

from pathlib import Path
from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="AION",
    settings_files=[Path(__file__).resolve().parent.parent.parent / "aion_api_client.yaml"],
)
