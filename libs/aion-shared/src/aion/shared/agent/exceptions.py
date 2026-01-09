class AdapterError(Exception):
    pass

class AdapterNotFoundError(AdapterError):
    def __init__(self, framework_name: str | None = None, agent_type: str | None = None):
        if framework_name:
            message = f"No adapter registered for framework '{framework_name}'"
        elif agent_type:
            message = f"No adapter can handle agent of type '{agent_type}'"
        else:
            message = "No suitable adapter found"
        super().__init__(message)

class AdapterRegistrationError(AdapterError):
    pass

class ExecutionError(AdapterError):
    pass

class StateRetrievalError(AdapterError):
    pass

class MessageConversionError(AdapterError):
    pass

class ConfigurationError(AdapterError):
    pass

class UnsupportedOperationError(AdapterError):
    def __init__(self, operation: str, framework: str):
        message = f"Operation '{operation}' is not supported by '{framework}' adapter"
        super().__init__(message)


