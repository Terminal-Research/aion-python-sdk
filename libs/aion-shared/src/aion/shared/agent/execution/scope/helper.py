from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, Dict, TYPE_CHECKING

from .types import AgentExecutionScope

if TYPE_CHECKING:
    from aion.shared.types.a2a.extensions import DistributionExtensionV1, TraceabilityExtensionV1
    from aion.shared.tasks.protocols import AionTaskManagerProtocol

__all__ = [
    "AgentExecutionScopeHelper",
]

_execution_scope_var: ContextVar[Optional[AgentExecutionScope]] = ContextVar(
    'agent_execution_scope', default=None
)


class AgentExecutionScopeHelper:
    """Static helpers for managing AgentExecutionScope.

    Provides the exclusive interface for managing the execution scope. All access to
    the scope must go through this helper to ensure consistency and proper lifecycle
    management. The underlying ContextVar is private and should not be accessed directly.
    """

    @staticmethod
    def set_scope(scope: Optional[AgentExecutionScope] = None) -> AgentExecutionScope:
        """Initialize execution scope for the current agent execution.

        Sets up the scope that will be shared across all async tasks within this execution.
        Should be called once per agent execution at its entry point (typically in middleware
        or request handler) to establish the execution context. Must be paired with a
        corresponding clear_scope() call at the end of execution.

        Args:
            scope: Optional pre-created AgentExecutionScope. If None, creates a new one.

        Returns:
            The set AgentExecutionScope instance.
        """
        if scope is None:
            scope = AgentExecutionScope()
        _execution_scope_var.set(scope)
        return scope

    @staticmethod
    def get_scope() -> Optional[AgentExecutionScope]:
        """Get current execution scope.

        Returns:
            The current AgentExecutionScope if set, otherwise None.
        """
        return _execution_scope_var.get()

    @staticmethod
    def clear_scope() -> None:
        """Clear current scope."""
        _execution_scope_var.set(None)

    @staticmethod
    def set_scope_from_a2a(
            distribution: Optional['DistributionExtensionV1'] = None,
            traceability: Optional['TraceabilityExtensionV1'] = None,
            request_method: Optional[str] = None,
            request_path: Optional[str] = None,
            jrpc_method: Optional[str] = None,
    ) -> AgentExecutionScope:
        """Initialize execution scope from A2A protocol extensions.

        Creates a new AgentExecutionScope with data extracted from A2A extensions.
        Should be called at request entry point (in middleware) when A2A metadata
        is available.

        Args:
            distribution: Optional DistributionExtensionV1 with agent deployment info
            traceability: Optional TraceabilityExtensionV1 with tracing context
            request_method: HTTP request method (defaults to "POST")
            request_path: HTTP request path (defaults to "/")
            jrpc_method: JSON-RPC method name if applicable

        Returns:
            The initialized AgentExecutionScope.
        """
        scope = AgentExecutionScope()

        # Set distribution/aion metadata
        if distribution:
            scope.inbound.aion.distribution_id = distribution.distribution.id
            scope.inbound.aion.version_id = distribution.behavior.version_id
            scope.inbound.aion.environment_id = distribution.environment.id

        # Set tracing data
        if traceability:
            scope.inbound.trace.traceparent = traceability.traceparent
            scope.inbound.trace.baggage = traceability.baggage or {}

        # Set request metadata
        scope.inbound.request.method = request_method or "POST"
        scope.inbound.request.path = request_path or "/"
        scope.inbound.request.jrpc_method = jrpc_method

        _execution_scope_var.set(scope)
        return scope

    @staticmethod
    def set_task_id(task_id: Optional[str]) -> AgentExecutionScope:
        """Set the task ID in the scope.

        Args:
            task_id: The task ID to set.

        Returns:
            The current AgentExecutionScope.

        Raises:
            RuntimeError: If no scope is currently set.
        """
        scope = AgentExecutionScopeHelper.get_scope()
        if scope is None:
            raise RuntimeError("No scope is currently set. Use set_scope() first.")
        scope.inbound.a2a.task_id = task_id
        return scope

    @staticmethod
    def set_task_status(task_status: Optional[str]) -> AgentExecutionScope:
        """Set the current task status in the scope.

        Args:
            task_status: The task status to set.

        Returns:
            The current AgentExecutionScope.

        Raises:
            RuntimeError: If no scope is currently set.
        """
        scope = AgentExecutionScopeHelper.get_scope()
        if scope is None:
            raise RuntimeError("No scope is currently set. Use set_scope() first.")
        scope.inbound.a2a.task_status = task_status
        return scope

    @staticmethod
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
        scope = AgentExecutionScopeHelper.get_scope()
        if scope is None:
            raise RuntimeError("No scope is currently set. Use set_scope() first.")

        if update:
            scope.framework.agent_framework.trace.baggage.update(baggage)
        else:
            scope.framework.agent_framework.trace.baggage = baggage

        return scope

    @staticmethod
    def set_task_manager(task_manager: AionTaskManagerProtocol) -> AgentExecutionScope:
        """Store the task manager in server runtime for use throughout execution.

        Makes the task manager accessible from anywhere within the execution scope
        without requiring it to be passed through function parameters. Typically called
        early in the execution pipeline after the task manager is instantiated.

        Args:
            task_manager: The task manager instance (typically AionTaskManager).

        Returns:
            The current AgentExecutionScope.

        Raises:
            RuntimeError: If no scope is currently set. Use set_scope() first.
        """
        scope = AgentExecutionScopeHelper.get_scope()
        if scope is None:
            raise RuntimeError("No scope is currently set. Use set_scope() first.")
        scope.server.task_manager = task_manager
        return scope

    @staticmethod
    def get_task_manager() -> Optional[AionTaskManagerProtocol]:
        """Get the task manager from server runtime.

        Returns:
            The task manager instance if set, otherwise None.

        Raises:
            RuntimeError: If no scope is currently set.
        """
        scope = AgentExecutionScopeHelper.get_scope()
        if scope is None:
            raise RuntimeError("No scope is currently set. Use set_scope() first.")
        return scope.server.task_manager
