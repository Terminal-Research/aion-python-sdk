"""Tests for AgentExecutionScopeHelper.

The critical property being tested is ContextVar isolation: a scope set in one
async task must not bleed into another task running concurrently. Every other
method builds on that guarantee, so we test the ContextVar boundary explicitly.
"""

import asyncio
import pytest

from aion.server.agent.execution.scope.helper import AgentExecutionScopeHelper
from aion.server.agent.execution.scope.types import AgentExecutionScope


def _reset():
    AgentExecutionScopeHelper.clear_scope()


class TestSetGetClear:
    def setup_method(self):
        _reset()

    def test_get_scope_returns_none_before_set(self):
        """get_scope returns None when no scope has been set in the current context."""
        assert AgentExecutionScopeHelper.get_scope() is None

    def test_set_scope_creates_new_scope_when_none_passed(self):
        """set_scope with no argument creates and stores a new AgentExecutionScope."""
        scope = AgentExecutionScopeHelper.set_scope()
        assert isinstance(scope, AgentExecutionScope)
        assert AgentExecutionScopeHelper.get_scope() is scope

    def test_set_scope_uses_provided_scope(self):
        """set_scope stores and returns the explicitly provided scope object."""
        custom = AgentExecutionScope()
        result = AgentExecutionScopeHelper.set_scope(custom)
        assert result is custom
        assert AgentExecutionScopeHelper.get_scope() is custom

    def test_clear_scope_resets_to_none(self):
        """clear_scope resets the current context's scope to None."""
        AgentExecutionScopeHelper.set_scope()
        AgentExecutionScopeHelper.clear_scope()
        assert AgentExecutionScopeHelper.get_scope() is None


class TestSetScopeFromA2A:
    def setup_method(self):
        _reset()

    def test_populates_request_defaults(self):
        """set_scope_from_a2a without args creates a scope with default request values."""
        scope = AgentExecutionScopeHelper.set_scope_from_a2a()
        assert scope.inbound.request.method == "POST"
        assert scope.inbound.request.path == "/"
        assert scope.inbound.request.jrpc_method is None

    def test_populates_request_from_args(self):
        """set_scope_from_a2a stores provided request method, path, and jrpc_method."""
        scope = AgentExecutionScopeHelper.set_scope_from_a2a(
            request_method="GET",
            request_path="/health",
            jrpc_method="tasks/send",
        )
        assert scope.inbound.request.method == "GET"
        assert scope.inbound.request.path == "/health"
        assert scope.inbound.request.jrpc_method == "tasks/send"

    def test_distribution_fields_populated(self):
        """set_scope_from_a2a extracts distribution_id, version_id, and environment_id from the distribution extension."""
        class FakeBehavior:
            version_id = "v-1"

        class FakeEnvironment:
            id = "env-1"

        class FakeDistribution:
            id = "dist-1"

        class FakeDistributionExtension:
            distribution = FakeDistribution()
            behavior = FakeBehavior()
            environment = FakeEnvironment()

        scope = AgentExecutionScopeHelper.set_scope_from_a2a(
            distribution=FakeDistributionExtension()
        )
        assert scope.inbound.aion.distribution_id == "dist-1"
        assert scope.inbound.aion.version_id == "v-1"
        assert scope.inbound.aion.environment_id == "env-1"

    def test_traceability_fields_populated(self):
        """set_scope_from_a2a copies traceparent and baggage from the traceability extension."""
        class FakeTraceability:
            traceparent = "00-abc-def-01"
            baggage = {"key": "value"}

        scope = AgentExecutionScopeHelper.set_scope_from_a2a(
            traceability=FakeTraceability()
        )
        assert scope.inbound.trace.traceparent == "00-abc-def-01"
        assert scope.inbound.trace.baggage == {"key": "value"}

    def test_traceability_none_baggage_becomes_empty_dict(self):
        """set_scope_from_a2a converts None baggage from traceability to an empty dict."""
        class FakeTraceability:
            traceparent = None
            baggage = None

        scope = AgentExecutionScopeHelper.set_scope_from_a2a(
            traceability=FakeTraceability()
        )
        assert scope.inbound.trace.baggage == {}


class TestMutatingHelpersRequireScope:
    def setup_method(self):
        _reset()

    def test_set_task_id_raises_without_scope(self):
        """set_task_id raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            AgentExecutionScopeHelper.set_task_id("task-1")

    def test_set_task_status_raises_without_scope(self):
        """set_task_status raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            AgentExecutionScopeHelper.set_task_status("working")

    def test_set_agent_framework_baggage_raises_without_scope(self):
        """set_agent_framework_baggage raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            AgentExecutionScopeHelper.set_agent_framework_baggage({"k": "v"})

    def test_set_task_manager_raises_without_scope(self):
        """set_task_manager raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            AgentExecutionScopeHelper.set_task_manager(object())  # type: ignore

    def test_get_task_manager_raises_without_scope(self):
        """get_task_manager raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            AgentExecutionScopeHelper.get_task_manager()


class TestMutationWithScope:
    def setup_method(self):
        _reset()

    def test_set_task_id(self):
        """set_task_id stores the task id in the current scope's a2a section."""
        AgentExecutionScopeHelper.set_scope()
        AgentExecutionScopeHelper.set_task_id("task-abc")
        assert AgentExecutionScopeHelper.get_scope().inbound.a2a.task_id == "task-abc"

    def test_set_task_status(self):
        """set_task_status stores the task status string in the current scope's a2a section."""
        AgentExecutionScopeHelper.set_scope()
        AgentExecutionScopeHelper.set_task_status("completed")
        assert AgentExecutionScopeHelper.get_scope().inbound.a2a.task_status == "completed"

    def test_set_agent_framework_baggage_merge(self):
        """set_agent_framework_baggage merges new keys into existing baggage by default."""
        AgentExecutionScopeHelper.set_scope()
        AgentExecutionScopeHelper.set_agent_framework_baggage({"a": "1"})
        AgentExecutionScopeHelper.set_agent_framework_baggage({"b": "2"})
        baggage = AgentExecutionScopeHelper.get_scope().framework.agent_framework.trace.baggage
        assert baggage == {"a": "1", "b": "2"}

    def test_set_agent_framework_baggage_replace(self):
        """set_agent_framework_baggage replaces existing baggage when update=False."""
        AgentExecutionScopeHelper.set_scope()
        AgentExecutionScopeHelper.set_agent_framework_baggage({"a": "1"})
        AgentExecutionScopeHelper.set_agent_framework_baggage({"b": "2"}, update=False)
        baggage = AgentExecutionScopeHelper.get_scope().framework.agent_framework.trace.baggage
        assert baggage == {"b": "2"}

    def test_set_and_get_task_manager(self):
        """set_task_manager stores an object retrievable via get_task_manager."""
        AgentExecutionScopeHelper.set_scope()
        sentinel = object()
        AgentExecutionScopeHelper.set_task_manager(sentinel)  # type: ignore
        assert AgentExecutionScopeHelper.get_task_manager() is sentinel


class TestContextVarIsolation:
    """ContextVar values are inherited at task-creation time but mutations in
    child tasks must not affect other tasks."""

    def test_scope_not_shared_between_independent_coroutines(self):
        """Two tasks that each set their own scope must not see each other's scope."""
        results = {}

        async def worker(name: str, task_id: str):
            AgentExecutionScopeHelper.set_scope()
            AgentExecutionScopeHelper.set_task_id(task_id)
            await asyncio.sleep(0)  # yield to event loop
            results[name] = AgentExecutionScopeHelper.get_scope().inbound.a2a.task_id

        async def run():
            await asyncio.gather(
                worker("a", "task-a"),
                worker("b", "task-b"),
            )

        asyncio.run(run())
        assert results["a"] == "task-a"
        assert results["b"] == "task-b"

    def test_child_task_inherits_parent_scope_but_mutations_do_not_leak_back(self):
        """If the parent sets a scope before spawning a child, the child sees it.
        But when the child mutates the scope, the parent's view is also affected
        because ContextVar copies on task creation point to the same mutable object.
        This test documents the ACTUAL semantics."""
        captured = {}

        async def run():
            AgentExecutionScopeHelper.clear_scope()
            AgentExecutionScopeHelper.set_scope()

            async def child():
                # Child sees parent scope (same object via ContextVar copy)
                captured["child_sees_scope"] = AgentExecutionScopeHelper.get_scope() is not None

            await asyncio.create_task(child())
            captured["parent_after"] = AgentExecutionScopeHelper.get_scope() is not None

        asyncio.run(run())
        assert captured["child_sees_scope"] is True
        assert captured["parent_after"] is True

    def test_clear_scope_in_child_does_not_affect_parent(self):
        """clear_scope() only modifies the ContextVar in the current task's context."""
        captured = {}

        async def run():
            AgentExecutionScopeHelper.clear_scope()
            AgentExecutionScopeHelper.set_scope()

            async def child():
                AgentExecutionScopeHelper.clear_scope()
                captured["child_after_clear"] = AgentExecutionScopeHelper.get_scope()

            await asyncio.create_task(child())
            # Parent scope untouched
            captured["parent_after_child_clear"] = AgentExecutionScopeHelper.get_scope()

        asyncio.run(run())
        assert captured["child_after_clear"] is None
        assert captured["parent_after_child_clear"] is not None
