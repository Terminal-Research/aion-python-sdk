"""Executable extensions: their executors and their registration."""

from .executable import ExecutableExtensionDescriptor, ExtensionExecutor, parse_options
from .registration import (
    EXECUTABLE_EXTENSIONS,
    EnabledExtension,
    create_extension_executors,
    register_extensions,
)

__all__ = [
    "EXECUTABLE_EXTENSIONS",
    "EnabledExtension",
    "ExecutableExtensionDescriptor",
    "ExtensionExecutor",
    "create_extension_executors",
    "parse_options",
    "register_extensions",
]
