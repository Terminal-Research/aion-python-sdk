from typing import Optional, Dict, Any, Union

from a2a.server.agent_execution import RequestContext as A2ARequestContext

from .request_context import RequestContext, request_context_var

__all__ = [
    "set_context",
    "update_context",
    "get_context",
    "clear_context",
    "set_context_from_a2a_request",
    "set_langgraph_node",
]


# Public API functions
def set_context(
        context: Optional[Union[RequestContext, Dict[str, Any]]] = None,
        **kwargs
) -> RequestContext:
    """
    Set request context from RequestContext object, dict, or individual parameters.

    Args:
        context: RequestContext object or dictionary with context data
        **kwargs: Individual context parameters (override context if provided)

    Returns:
        The set RequestContext instance
    """
    # Start with empty context data
    context_data = {}

    # Add data from context parameter
    if context is not None:
        if isinstance(context, RequestContext):
            context_data.update(context.to_dict())
        elif isinstance(context, dict):
            context_data.update(context)
        else:
            raise TypeError("context must be RequestContext instance or dict")

    # Override with kwargs (kwargs have higher priority)
    context_data.update({k: v for k, v in kwargs.items() if v is not None})

    # Create RequestContext instance
    if context_data:
        new_context = RequestContext.from_dict(context_data)
    else:
        new_context = RequestContext()  # Will generate default request_id

    # Set in context variable
    request_context_var.set(new_context)

    return new_context


def get_context() -> Optional[RequestContext]:
    """
    Get current request context.

    Returns:
        Current RequestContext or None if not set
    """
    return request_context_var.get()


def update_context(**kwargs) -> RequestContext:
    """
    Update current context with new values.

    Args:
        **kwargs: Context parameters to update

    Returns:
        Updated RequestContext instance

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


def set_langgraph_node(node_name: str) -> RequestContext:
    """
    Set the current LangGraph node name in the context.

    Args:
        node_name: Name of the current LangGraph node

    Returns:
        Updated RequestContext instance

    Raises:
        RuntimeError: If no context is currently set
    """
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")

    current.set_langgraph_current_node(node_name)
    return current


def set_context_from_a2a_request(
        metadata: Dict[str, Any],
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        jrpc_method: Optional[str] = None
) -> RequestContext:
    """
    Set the request context from A2A request data.

    Extracts relevant information from A2A request metadata including distribution,
    behavior, and environment details, and creates a RequestContext object. The context
    is then set in the context variable and returned.

    Args:
        metadata: A2A request metadata containing trace ID, sender ID, distribution,
                  behavior, and environment information
        request_method: Optional HTTP request method (e.g., 'GET', 'POST')
        request_path: Optional request path/endpoint
        jrpc_method: Optional JSON-RPC method name

    Returns:
        RequestContext: The created and set request context with all extracted
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

