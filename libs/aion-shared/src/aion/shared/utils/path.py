import os
from pathlib import Path


def get_base_dir() -> Path:
    """Return the current working directory as the base directory.

    This function is used as a default root directory for resolving
    relative paths in the SDK context.

    Returns:
        Path: The absolute path to the current working directory.
    """
    return Path(os.getcwd())


def get_config_path(config_path: str | Path = "aion.yaml") -> Path:
    """Resolve the full path to the configuration file.

    If the given path is relative, it is resolved against the base
    directory (the current working directory).

    Args:
        config_path: Path to the configuration file. Can be absolute
            or relative. Defaults to "aion.yaml".

    Returns:
        Path: Absolute path to the configuration file.
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = get_base_dir() / path
    return path
