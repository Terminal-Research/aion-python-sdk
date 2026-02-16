from typing import Optional

from aion.shared.types.a2a.extensions import DistributionExtensionV1, TraceabilityExtensionV1
from .execution_context import ExecutionContext, RequestContext, TraceContext, AionContext, execution_context_var

__all__ = [
    "set_context",
    "get_context",
    "clear_context",
    "set_context_from_a2a",
    "set_current_node",
    "set_task_id",
    "set_task_status",
]


def set_context(context: Optional[ExecutionContext] = None) -> ExecutionContext:
    """
    Set execution context for the current request.
    Creates a new ExecutionContext if none provided.
    Should be called once per request to establish the shared mutable context.
    """
    if context is None:
        context = ExecutionContext()

    execution_context_var.set(context)
    return context


def get_context() -> Optional[ExecutionContext]:
    """Get current execution context."""
    return execution_context_var.get()


def clear_context():
    """Clear current context."""
    execution_context_var.set(None)


def set_current_node(node_name: str) -> ExecutionContext:
    """Set the current executing node name in the context."""
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")
    current.current_node = node_name
    return current


def set_task_id(task_id: Optional[str]) -> ExecutionContext:
    """Set the task ID in the context."""
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")
    current.a2a.task_id = task_id
    return current


def set_task_status(task_status: Optional[str] = None) -> ExecutionContext:
    """Set the current task status in the context."""
    current = get_context()
    if current is None:
        raise RuntimeError("No context is currently set. Use set_context() first.")
    current.a2a.task_status = task_status
    return current


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
