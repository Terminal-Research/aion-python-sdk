class AgentAdapterError(Exception):
    pass

class AgentAdapterNotFoundError(AgentAdapterError):
    def __init__(self, framework_name: str | None = None, agent_type: str | None = None):
        if framework_name:
            message = f"No adapter registered for framework '{framework_name}'"
        elif agent_type:
            message = f"No adapter can handle agent of type '{agent_type}'"
        else:
            message = "No suitable adapter found"
        super().__init__(message)

class AgentAdapterRegistrationError(AgentAdapterError):
    pass

class AgentExecutionError(AgentAdapterError):
    pass

class AgentStateRetrievalError(AgentAdapterError):
    pass

class AgentCheckpointError(AgentAdapterError):
    pass

class AgentMessageConversionError(AgentAdapterError):
    pass

class AgentConfigurationError(AgentAdapterError):
    pass

class AgentUnsupportedOperationError(AgentAdapterError):
    def __init__(self, operation: str, framework: str):
        message = f"Operation '{operation}' is not supported by '{framework}' adapter"
        super().__init__(message)


