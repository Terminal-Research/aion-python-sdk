"""ContextVar-backed helpers for reading and mutating the per-request AgentExecutionScope."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, Dict, TYPE_CHECKING

from .types import AgentExecutionScope

if TYPE_CHECKING:
    from aion.core.a2a.extensions import DistributionExtensionV1, TraceabilityExtensionV1
    from aion.server.tasks.protocols import AionTaskManagerProtocol

__all__ = [
    "init_execution_scope",
    "get_execution_scope",
    "clear_execution_scope",
    "set_distribution",
    "set_traceability",
    "set_request",
    "set_task_id",
    "set_task_status",
    "set_agent_framework_baggage",
    "set_task_manager",
    "get_task_manager",
]

_execution_scope_var: ContextVar[Optional[AgentExecutionScope]] = ContextVar(
    'agent_execution_scope', default=None
)  # Isolated per async task; reset to None between requests.


def get_execution_scope() -> Optional[AgentExecutionScope]:
    """Get current execution scope.

    Returns:
        The current AgentExecutionScope if set, otherwise None.
    """
    return _execution_scope_var.get()


def init_execution_scope(scope: Optional[AgentExecutionScope] = None) -> AgentExecutionScope:
    """Initialize execution scope for the current agent execution.

    Sets up the scope that will be shared across all async tasks within this execution.
    Should be called once per agent execution at its entry point (typically in middleware
    or request handler) to establish the execution context. Must be paired with a
    corresponding clear_execution_scope() call at the end of execution.

    Args:
        scope: Optional pre-created AgentExecutionScope. If None, creates a new one.

    Returns:
        The set AgentExecutionScope instance.
    """
    if scope is None:
        scope = AgentExecutionScope()
    _execution_scope_var.set(scope)
    return scope


def clear_execution_scope() -> None:
    """Clear current scope."""
    _execution_scope_var.set(None)


def set_distribution(distribution: 'DistributionExtensionV1') -> AgentExecutionScope:
    """Populate distribution metadata from DistributionExtensionV1.

    Extracts and stores distribution_id, version_id, and environment_id from the
    distribution extension into the current scope's inbound data.

    Args:
        distribution: DistributionExtensionV1 with agent deployment info.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")

    scope.inbound.aion.distribution_id = distribution.distribution.id
    scope.inbound.aion.version_id = distribution.behavior.version_id
    scope.inbound.aion.environment_id = distribution.environment.id

    return scope


def set_traceability(traceability: 'TraceabilityExtensionV1') -> AgentExecutionScope:
    """Populate tracing data from TraceabilityExtensionV1.

    Extracts and stores traceparent and baggage from the traceability extension
    into the current scope's inbound trace data.

    Args:
        traceability: TraceabilityExtensionV1 with tracing context.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")

    scope.inbound.trace.traceparent = traceability.traceparent
    scope.inbound.trace.baggage = traceability.baggage or {}

    return scope


def set_request(
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        jrpc_method: Optional[str] = None,
) -> AgentExecutionScope:
    """Populate HTTP request metadata.

    Stores HTTP method, path, and JSON-RPC method into the current scope's
    inbound request data.

    Args:
        request_method: HTTP request method (defaults to "POST" if not set).
        request_path: HTTP request path (defaults to "/" if not set).
        jrpc_method: JSON-RPC method name if applicable.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")

    if request_method is not None:
        scope.inbound.request.method = request_method
    if request_path is not None:
        scope.inbound.request.path = request_path
    if jrpc_method is not None:
        scope.inbound.request.jrpc_method = jrpc_method

    return scope


def set_task_id(task_id: str) -> AgentExecutionScope:
    """Set the task ID in the scope.

    Args:
        task_id: The task ID to set.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")
    scope.inbound.a2a.task_id = task_id
    return scope


def set_task_status(task_status: str) -> AgentExecutionScope:
    """Set the current task status in the scope.

    Args:
        task_status: The task status to set.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")
    scope.inbound.a2a.task_status = task_status
    return scope


def set_agent_framework_baggage(baggage: Dict[str, str], update: bool = True) -> AgentExecutionScope:
    """Set or update agent framework trace baggage.

    Args:
        baggage: Dictionary of baggage entries.
        update: If True, merge with existing baggage (default).
               If False, replace entire baggage.

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")

    if update:
        scope.framework.agent_framework.trace.baggage.update(baggage)
    else:
        scope.framework.agent_framework.trace.baggage = baggage

    return scope


def set_task_manager(task_manager: 'AionTaskManagerProtocol') -> AgentExecutionScope:
    """Store the task manager in execution runtime for use throughout execution.

    Makes the task manager accessible from anywhere within the execution scope
    without requiring it to be passed through function parameters. Typically called
    early in the execution pipeline after the task manager is instantiated.

    Args:
        task_manager: The task manager instance (typically AionTaskManager).

    Returns:
        The current AgentExecutionScope.

    Raises:
        RuntimeError: If no scope is currently set. Use set_execution_scope() first.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")
    scope.runtime.task_manager = task_manager
    return scope


def get_task_manager() -> Optional['AionTaskManagerProtocol']:
    """Get the task manager from execution runtime.

    Returns:
        The task manager instance if set, otherwise None.

    Raises:
        RuntimeError: If no scope is currently set.
    """
    scope = get_execution_scope()
    if scope is None:
        raise RuntimeError("No scope is currently set. Use set_execution_scope() first.")
    return scope.runtime.task_manager



