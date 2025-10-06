from typing import Optional, Dict, Any, Union

from a2a.server.agent_execution import RequestContext as A2ARequestContext

from .request_context import RequestContext, request_context_var

__all__ = [
    "set_context",
    "update_context",
    "get_context",
    "clear_context",
    "set_context_from_a2a_request",
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
    current = request_context_var.get()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")

    updated = current.update(**kwargs)
    request_context_var.set(updated)
    return updated


def clear_context():
    """Clear current context (useful for testing)"""
    request_context_var.set(None)


def set_context_from_a2a_request(
        metadata: Dict[str, Any],
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        jrpc_method: Optional[str] = None
) -> RequestContext:
    """
    Set the request context from A2A request data.

    Args:
        metadata: A2A request metadata

    Returns:
        RequestContext: The created and set request context
    """
    # Extract distribution information
    distribution = metadata.get("aion:distribution", {})
    behavior = metadata.get("aion:behavior", {})
    environment = metadata.get("aion:environment", {})

    # Create the request context directly
    request_context = RequestContext(
        trace_id=metadata.get("aion:traceId"),
        user_id=metadata.get("aion:senderId"),
        aion_distribution_id=distribution.get("id"),
        aion_version_id=behavior.get("versionId"),
        aion_agent_environment_id=environment.get("id")
    )

    if request_method is not None:
        request_context.request_method = request_method

    if request_path is not None:
        request_context.request_path = request_path

    if jrpc_method is not None:
        request_context.request_jrpc_method = jrpc_method

    # Set the context in the context variable
    set_context(request_context)
    return request_context
