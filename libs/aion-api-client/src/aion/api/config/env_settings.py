"""Configuration for the Aion API client."""

from pathlib import Path
from dynaconf import Dynaconf, Validator

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
BASE_DIR = ROOT_DIR / "src" / "aion" / "api"

settings = Dynaconf(
    envvar_prefix="AION",
    settings_files=[
        (ROOT_DIR / "aion_api_client.yaml"),
    ],
    environments=True,
    env="production",
    ENV_SWITCHER_FOR_DYNACONF="AION_API_CLIENT_ENV",
    validators = [
        # settings from .env
        Validator("CLIENT_ID", must_exist=True, is_type_of=str),
        Validator("CLIENT_SECRET", must_exist=True, is_type_of=str),

        # settings from aion_api_client.yaml
        Validator("aion_api.host", must_exist=True, is_type_of=str),
        Validator("aion_api.port", must_exist=True, is_type_of=int, gte=1, lte=65535),
        Validator("aion_api.keepalive", must_exist=True, is_type_of=int, gte=5),
    ]
)
