import os
from pathlib import Path


def get_base_dir() -> Path:
    """Return the current working directory as the base directory."""
    return Path(os.getcwd())


def get_config_path(config_path: str | Path = "aion.yaml") -> Path:
    """Resolve the full path to the configuration file."""
    path = Path(config_path)
    if not path.is_absolute():
        path = get_base_dir() / path
    return path
