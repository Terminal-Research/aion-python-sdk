import os
import re
from pathlib import Path
from typing import Any, Dict


def substitute_vars(template: str, values: Dict[str, Any]) -> str:
    """
    Replace variables in a template string with provided values.

    Args:
        template: Template string containing placeholders.
        values: Dictionary with values to substitute.

    Returns:
        String with substituted values.
    """

    def replacer(match: re.Match) -> str:
        # Extract variable name (ignore optional type part)
        var_name = match.group(1)
        # Replace with provided value or keep original placeholder if not found
        return str(values.get(var_name, match.group(0)))

    # Regex captures {var_name} or {var_name:type}
    return re.sub(r"{(\w+)(:[^}]*)?}", replacer, template)


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
