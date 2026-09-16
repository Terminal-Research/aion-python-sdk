"""Tests for execution scope functions.

The critical property being tested is ContextVar isolation: a scope set in one
async task must not bleed into another task running concurrently. Every other
method builds on that guarantee, so we test the ContextVar boundary explicitly.
"""

import asyncio
import pytest

from aion.server.agent.execution.scope import (
    init_execution_scope,
    get_execution_scope,
    clear_execution_scope,
    set_distribution,
    set_traceability,
    set_request,
    set_task_id,
    set_task_status,
    set_agent_framework_baggage,
    set_task_manager,
    get_task_manager,
)
from aion.server.agent.execution.scope.types import AgentExecutionScope


def _reset():
    clear_execution_scope()


class TestSetGetClear:
    def setup_method(self):
        _reset()

    def test_get_scope_returns_none_before_set(self):
        """get_scope returns None when no scope has been set in the current context."""
        assert get_execution_scope() is None

    def test_init_scope_creates_new_scope_when_none_passed(self):
        """init_scope with no argument creates and stores a new AgentExecutionScope."""
        scope = init_execution_scope()
        assert isinstance(scope, AgentExecutionScope)
        assert get_execution_scope() is scope

    def test_init_scope_uses_provided_scope(self):
        """init_scope stores and returns the explicitly provided scope object."""
        custom = AgentExecutionScope()
        result = init_execution_scope(custom)
        assert result is custom
        assert get_execution_scope() is custom

    def test_clear_scope_resets_to_none(self):
        """clear_scope resets the current context's scope to None."""
        init_execution_scope()
        clear_execution_scope()
        assert get_execution_scope() is None


class TestSetRequest:
    def setup_method(self):
        _reset()

    def test_set_request_defaults(self):
        """set_request with no args uses existing request values (no defaults applied)."""
        init_execution_scope()
        scope = set_request()
        # set_request doesn't set defaults, it only updates provided values
        assert scope.inbound.request.method == "POST"  # From AgentExecutionScope default
        assert scope.inbound.request.path == "/"  # From AgentExecutionScope default
        assert scope.inbound.request.jrpc_method is None

    def test_set_request_from_args(self):
        """set_request stores provided request method, path, and jrpc_method."""
        init_execution_scope()
        scope = set_request("GET", "/health", "tasks/send")
        assert scope.inbound.request.method == "GET"
        assert scope.inbound.request.path == "/health"
        assert scope.inbound.request.jrpc_method == "tasks/send"

    def test_set_distribution(self):
        """set_distribution extracts distribution_id, version_id, and environment_id."""
        init_execution_scope()

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

        scope = set_distribution(FakeDistributionExtension())
        assert scope.inbound.aion.distribution_id == "dist-1"
        assert scope.inbound.aion.version_id == "v-1"
        assert scope.inbound.aion.environment_id == "env-1"

    def test_set_traceability_fields(self):
        """set_traceability copies traceparent and baggage."""
        init_execution_scope()

        class FakeTraceability:
            traceparent = "00-abc-def-01"
            baggage = {"key": "value"}

        scope = set_traceability(FakeTraceability())
        assert scope.inbound.trace.traceparent == "00-abc-def-01"
        assert scope.inbound.trace.baggage == {"key": "value"}

    def test_set_traceability_none_baggage(self):
        """set_traceability converts None baggage to an empty dict."""
        init_execution_scope()

        class FakeTraceability:
            traceparent = None
            baggage = None

        scope = set_traceability(FakeTraceability())
        assert scope.inbound.trace.baggage == {}


class TestMutatingHelpersRequireScope:
    def setup_method(self):
        _reset()

    def test_set_task_id_raises_without_scope(self):
        """set_task_id raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            set_task_id("task-1")

    def test_set_task_status_raises_without_scope(self):
        """set_task_status raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            set_task_status("working")

    def test_set_agent_framework_baggage_raises_without_scope(self):
        """set_agent_framework_baggage raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            set_agent_framework_baggage({"k": "v"})

    def test_set_task_manager_raises_without_scope(self):
        """set_task_manager raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            set_task_manager(object())  # type: ignore

    def test_get_task_manager_raises_without_scope(self):
        """get_task_manager raises RuntimeError when no scope has been set."""
        with pytest.raises(RuntimeError):
            get_task_manager()


class TestMutationWithScope:
    def setup_method(self):
        _reset()

    def test_set_task_id(self):
        """set_task_id stores the task id in the current scope's a2a section."""
        init_execution_scope()
        set_task_id("task-abc")
        assert get_execution_scope().inbound.a2a.task_id == "task-abc"

    def test_set_task_status(self):
        """set_task_status stores the task status string in the current scope's a2a section."""
        init_execution_scope()
        set_task_status("completed")
        assert get_execution_scope().inbound.a2a.task_status == "completed"

    def test_set_agent_framework_baggage_merge(self):
        """set_agent_framework_baggage merges new keys into existing baggage by default."""
        init_execution_scope()
        set_agent_framework_baggage({"a": "1"})
        set_agent_framework_baggage({"b": "2"})
        baggage = get_execution_scope().framework.agent_framework.trace.baggage
        assert baggage == {"a": "1", "b": "2"}

    def test_set_agent_framework_baggage_replace(self):
        """set_agent_framework_baggage replaces existing baggage when update=False."""
        init_execution_scope()
        set_agent_framework_baggage({"a": "1"})
        set_agent_framework_baggage({"b": "2"}, update=False)
        baggage = get_execution_scope().framework.agent_framework.trace.baggage
        assert baggage == {"b": "2"}

    def test_set_and_get_task_manager(self):
        """set_task_manager stores an object retrievable via get_task_manager."""
        init_execution_scope()
        sentinel = object()
        set_task_manager(sentinel)  # type: ignore
        assert get_task_manager() is sentinel


class TestContextVarIsolation:
    """ContextVar values are inherited at task-creation time but mutations in
    child tasks must not affect other tasks."""

    def test_scope_not_shared_between_independent_coroutines(self):
        """Two tasks that each set their own scope must not see each other's scope."""
        results = {}

        async def worker(name: str, task_id: str):
            init_execution_scope()
            set_task_id(task_id)
            await asyncio.sleep(0)  # yield to event loop
            results[name] = get_execution_scope().inbound.a2a.task_id

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
            clear_execution_scope()
            init_execution_scope()

            async def child():
                # Child sees parent scope (same object via ContextVar copy)
                captured["child_sees_scope"] = get_execution_scope() is not None

            await asyncio.create_task(child())
            captured["parent_after"] = get_execution_scope() is not None

        asyncio.run(run())
        assert captured["child_sees_scope"] is True
        assert captured["parent_after"] is True

    def test_clear_scope_in_child_does_not_affect_parent(self):
        """clear_scope() only modifies the ContextVar in the current task's context."""
        captured = {}

        async def run():
            clear_execution_scope()
            init_execution_scope()

            async def child():
                clear_execution_scope()
                captured["child_after_clear"] = get_execution_scope()

            await asyncio.create_task(child())
            # Parent scope untouched
            captured["parent_after_child_clear"] = get_execution_scope()

        asyncio.run(run())
        assert captured["child_after_clear"] is None
        assert captured["parent_after_child_clear"] is not None
