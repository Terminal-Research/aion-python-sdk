import re
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
