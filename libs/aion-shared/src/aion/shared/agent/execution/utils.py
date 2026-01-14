from contextlib import contextmanager
from typing import Optional, Dict, Any, Union

from .context import ExecutionContext, request_context_var

__all__ = [
    "set_context",
    "update_context",
    "get_context",
    "clear_context",
    "set_context_from_a2a_request",
    "set_langgraph_node",
    "set_working_task",
    "task_context",
]


# Public API functions
def set_context(
        context: Optional[Union[ExecutionContext, Dict[str, Any]]] = None,
        **kwargs
) -> ExecutionContext:
    """
    Set execution context from ExecutionContext object, dict, or individual parameters.

    Args:
        context: ExecutionContext object or dictionary with context data
        **kwargs: Individual context parameters (override context if provided)

    Returns:
        The set ExecutionContext instance
    """
    # Start with empty context data
    context_data = {}

    # Add data from context parameter
    if context is not None:
        if isinstance(context, ExecutionContext):
            context_data.update(context.to_dict())
        elif isinstance(context, dict):
            context_data.update(context)
        else:
            raise TypeError("context must be ExecutionContext instance or dict")

    # Override with kwargs (kwargs have higher priority)
    context_data.update({k: v for k, v in kwargs.items() if v is not None})

    # Create ExecutionContext instance
    if context_data:
        new_context = ExecutionContext.from_dict(context_data)
    else:
        new_context = ExecutionContext()  # Will generate default transaction_id

    # Set in context variable
    request_context_var.set(new_context)

    return new_context


def get_context() -> Optional[ExecutionContext]:
    """
    Get current execution context.

    Returns:
        Current ExecutionContext or None if not set
    """
    return request_context_var.get()


def update_context(**kwargs) -> ExecutionContext:
    """
    Update current context with new values.

    Args:
        **kwargs: Context parameters to update

    Returns:
        Updated ExecutionContext instance

    Raises:
        RuntimeError: If no context is currently set
    """
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")

    return set_context(current, **kwargs)


def clear_context():
    """Clear current context (useful for testing)"""
    request_context_var.set(None)


def set_langgraph_node(node_name: str) -> ExecutionContext:
    """
    Set the current LangGraph node name in the context.

    Args:
        node_name: Name of the current LangGraph node

    Returns:
        Updated ExecutionContext instance

    Raises:
        RuntimeError: If no context is currently set
    """
    return update_context(langgraph_current_node=node_name)


def set_working_task(task_id: Optional[str] = None) -> ExecutionContext:
    """
    Set the current working task ID in the context.

    Args:
        task_id: ID of the current task (None to clear)

    Returns:
        Updated ExecutionContext instance

    Raises:
        RuntimeError: If no context is currently set
    """
    return update_context(task_id=task_id)


def set_context_from_a2a_request(
        metadata: Dict[str, Any],
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        jrpc_method: Optional[str] = None
) -> ExecutionContext:
    """
    Set the execution context from A2A request data.

    Extracts relevant information from A2A request metadata including distribution,
    behavior, and environment details, and creates an ExecutionContext object. The context
    is then set in the context variable and returned.

    Args:
        metadata: A2A request metadata containing trace ID, sender ID, distribution,
                  behavior, and environment information
        request_method: Optional HTTP request method (e.g., 'GET', 'POST')
        request_path: Optional request path/endpoint
        jrpc_method: Optional JSON-RPC method name

    Returns:
        ExecutionContext: The created and set execution context with all extracted
                         and provided information
    """
    distribution = metadata.get("aion:distribution", {})
    behavior = metadata.get("aion:behavior", {})
    environment = metadata.get("aion:environment", {})

    return set_context(
        trace_id=metadata.get("aion:traceId"),
        user_id=metadata.get("aion:senderId"),
        aion_distribution_id=distribution.get("id"),
        aion_version_id=behavior.get("versionId"),
        aion_agent_environment_id=environment.get("id"),
        request_method=request_method,
        request_path=request_path,
        request_jrpc_method=jrpc_method
    )


@contextmanager
def task_context(task_id: str):
    """
    Context manager for setting task_id in the request context.

    Automatically sets task_id when entering the context and ensures
    proper cleanup on exit. This is useful for managing task scope
    in execution flows.

    Args:
        task_id: ID of the task to set in context

    Yields:
        Updated ExecutionContext with task_id set

    Raises:
        RuntimeError: If no context is currently set
    """
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")

    # Save previous task_id
    previous_task_id = current.task_id

    # Set new task_id
    updated_context = update_context(task_id=task_id)

    try:
        yield updated_context
    finally:
        # Restore previous task_id
        update_context(task_id=previous_task_id)
