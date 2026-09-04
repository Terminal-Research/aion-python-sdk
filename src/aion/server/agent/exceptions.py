"""Agent adapter exception hierarchy for the Aion server."""


class AdapterError(Exception):
    """Base class for all agent adapter errors."""


class AdapterNotFoundError(AdapterError):
    """Raised when no adapter is registered for the requested framework or agent type."""

    def __init__(self, framework_name: str | None = None, agent_type: str | None = None):
        if framework_name:
            message = f"No adapter registered for framework '{framework_name}'"
        elif agent_type:
            message = f"No adapter can handle agent of type '{agent_type}'"
        else:
            message = "No suitable adapter found"
        super().__init__(message)


class AdapterRegistrationError(AdapterError):
    """Raised when an adapter cannot be registered (e.g., duplicate framework name)."""


class ExecutionError(AdapterError):
    """Raised when agent execution fails unexpectedly."""


class StateRetrievalError(AdapterError):
    """Raised when fetching or parsing the agent execution state fails."""


class MessageConversionError(AdapterError):
    """Raised when converting messages between A2A and framework formats fails."""


class ConfigurationError(AdapterError):
    """Raised when the agent configuration is invalid or missing required fields."""


class UnsupportedOperationError(AdapterError):
    """Raised when an operation is not supported by the current adapter."""

    def __init__(self, operation: str, framework: str):
        message = f"Operation '{operation}' is not supported by '{framework}' adapter"
        super().__init__(message)


