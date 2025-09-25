from typing import Literal

__all__ = [
    "colorize_text",
]



def colorize_text(
        text: str,
        color: Literal[
            "red", "green", "yellow", "orange", "blue", "magenta", "cyan", "light_grey", "bright_grey",
            "bright_red", "bright_green", "bright_yellow", "bright_blue", "reset"
        ] = "reset"
) -> str:
    """
    Colorize text for terminal output

    Args:
        text (str): Text to colorize
        color (str): Color name

    Returns:
        str: Colorized text
    """
    color_codes = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "orange": "\033[38;5;208m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "bright_grey": "\033[97m",
        "light_grey": "\033[37m",
        "bright_red": "\033[91m",
        "bright_green": "\033[92m",
        "bright_yellow": "\033[93m",
        "bright_blue": "\033[94m",
        "reset": "\033[0m",
    }
    color_prefix = color_codes.get(color, '')
    color_suffix = color_codes['reset']
    return f"{color_prefix}{text}{color_suffix}"
