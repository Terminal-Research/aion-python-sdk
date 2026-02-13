from contextlib import contextmanager
from typing import Optional

from aion.shared.types.a2a.extensions import DistributionExtensionV1, TraceabilityExtensionV1

from .execution_context import ExecutionContext, RequestContext, TraceContext, AionContext, request_context_var

__all__ = [
    "set_context",
    "update_context",
    "get_context",
    "clear_context",
    "set_context_from_a2a",
    "set_current_node",
    "set_working_task",
    "task_context",
]


def set_context(context: Optional[ExecutionContext] = None, **kwargs) -> ExecutionContext:
    """
    Set execution context from an ExecutionContext object or individual parameters.

    Args:
        context: ExecutionContext instance to set (creates a new one if None)
        **kwargs: Top-level fields to override on the context

    Returns:
        The set ExecutionContext instance
    """
    if context is None:
        context = ExecutionContext()

    if kwargs:
        context = context.update(**kwargs)

    request_context_var.set(context)
    return context


def get_context() -> Optional[ExecutionContext]:
    """Get current execution context."""
    return request_context_var.get()


def update_context(**kwargs) -> ExecutionContext:
    """
    Update current context with new values.

    Raises:
        RuntimeError: If no context is currently set
    """
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")
    return set_context(current, **kwargs)


def clear_context():
    """Clear current context."""
    request_context_var.set(None)


def set_current_node(node_name: str) -> ExecutionContext:
    """Set the current executing node name in the context."""
    return update_context(current_node=node_name)


def set_working_task(task_id: Optional[str] = None) -> ExecutionContext:
    """Set the current working task ID in the context."""
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")
    new_a2a = current.a2a.model_copy(update={"task_id": task_id})
    return set_context(current.update(a2a=new_a2a))


def set_context_from_a2a(
        distribution: Optional[DistributionExtensionV1] = None,
        traceability: Optional[TraceabilityExtensionV1] = None,
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        jrpc_method: Optional[str] = None,
) -> ExecutionContext:
    """Set the execution context from typed A2A extension objects."""
    traceparent, baggage = None, {}
    if traceability:
        traceparent = traceability.traceparent
        baggage = traceability.baggage or {}

    distribution_id, version_id, environment_id = None, None, None
    if distribution:
        distribution_id = distribution.distribution.id
        version_id = distribution.behavior.version_id
        environment_id = distribution.environment.id

    context = ExecutionContext(
        request=RequestContext(
            method=request_method or "POST",
            path=request_path or "/",
            jrpc_method=jrpc_method,
        ),
        trace=TraceContext(
            traceparent=traceparent,
            baggage=baggage,
        ),
        aion=AionContext(
            distribution_id=distribution_id,
            version_id=version_id,
            environment_id=environment_id,
        ),
    )
    return set_context(context)


@contextmanager
def task_context(task_id: str):
    """
    Context manager for scoping task_id in the execution context.

    Saves the previous task_id and restores it on exit.

    Raises:
        RuntimeError: If no context is currently set
    """
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")

    previous_task_id = current.a2a.task_id
    new_a2a = current.a2a.model_copy(update={"task_id": task_id})
    updated_context = set_context(current.update(a2a=new_a2a))

    try:
        yield updated_context
    finally:
        restore_a2a = get_context().a2a.model_copy(update={"task_id": previous_task_id})
        set_context(get_context().update(a2a=restore_a2a))
