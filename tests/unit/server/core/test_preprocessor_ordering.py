"""Where preprocessing sits in the request path, and what compensates it.

Preprocessors can have external side effects - storing an attachment into the
organization a request names. That makes their position load-bearing: metadata
that merely parsed has not been accepted, so verification must come first, and
a partially-done preprocessor must be compensated even when it is the one that
raised.
"""

from types import SimpleNamespace

import pytest
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.types import Message, Role, SendMessageRequest, Task
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.core.runtime.context import AionRuntimeExtensions
from aion.server.core.app.handlers import AionRequestHandler


class SpyPreprocessor:
    """Records what it was handed and, optionally, refuses the request."""

    def __init__(self, name: str = "spy", raises: Exception | None = None) -> None:
        self.name = name
        self._raises = raises
        self.calls: list = []
        self.rollbacks = 0

    async def process(self, request_obj, context) -> None:
        self.calls.append(context)
        if self._raises is not None:
            raise self._raises

    async def rollback(self) -> None:
        self.rollbacks += 1


class RouteRecorder:
    """An executor that routes every new task to one extension, records its admissions, and may refuse them."""

    URI = "https://example.invalid/toolkits/recorded/1.0.0"

    def __init__(self, raises: Exception | None = None) -> None:
        self.admitted: list = []
        self.continued: list = []
        self._raises = raises

    def routed_extension(self, extensions) -> str:
        return self.URI

    async def admit_new_task(self, uri, message, extensions, call_context) -> None:
        self.admitted.append(extensions)
        if self._raises is not None:
            raise self._raises

    def admit_continuation(self, task) -> None:
        self.continued.append(task)


def handler(*preprocessors, executor: RouteRecorder | None = None) -> AionRequestHandler:
    """An AionRequestHandler with only the fields this path touches.

    Built without ``__init__``: the full constructor wires a task store, an
    executor and a push sender, none of which take part in ordering.
    Admission into the context is recorded in ``admitted`` instead of run,
    and the route of a new task is admitted by a ``RouteRecorder``.
    """
    instance = AionRequestHandler.__new__(AionRequestHandler)
    instance._preprocessors = list(preprocessors)
    instance.admitted = 0
    instance.agent_executor = executor or RouteRecorder()
    instance._request_context_builder = None
    instance._abandoned_tasks = None

    async def admit(params, call_context):
        instance.admitted += 1
        task_id = params.message.task_id
        return Task(id=task_id, context_id="ctx-1") if task_id else None

    instance._admit = admit
    return instance


def params() -> SendMessageRequest:
    return SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))


def call_context() -> SimpleNamespace:
    return SimpleNamespace(requested_extensions=set(), state={})


@pytest.fixture
def accepting_super(monkeypatch):
    """Replace the base handler's setup with a recorder."""
    order: list[str] = []

    async def fake_setup(self, params, call_context):
        order.append("setup")
        return ("active-task", "request-context")

    monkeypatch.setattr(DefaultRequestHandlerV2, "_setup_active_task", fake_setup)
    return order


@pytest.fixture
def failing_super(monkeypatch):
    async def fake_setup(self, params, call_context):
        raise RuntimeError("downstream boom")

    monkeypatch.setattr(DefaultRequestHandlerV2, "_setup_active_task", fake_setup)


class TestOrdering:
    async def test_verification_runs_before_preprocessing(self, accepting_super):
        """A declaration the agent rejects must not reach a side-effecting preprocessor."""
        aion_a2a_extension_registry.reset_to_default()
        spy = SpyPreprocessor()
        request = params()
        request.message.extensions.append(DAEMON_EXTENSION_URI_V1)
        executor = RouteRecorder()

        with pytest.raises(InvalidParamsError):
            await handler(spy, executor=executor)._setup_active_task(request, call_context())

        assert executor.admitted == []
        assert spy.calls == []
        assert accepting_super == []

    async def test_the_route_of_a_new_task_is_admitted_before_preprocessing(self, accepting_super):
        """A route that refuses the request leaves no preprocessor side effect and no task."""
        aion_a2a_extension_registry.reset_to_default()
        spy = SpyPreprocessor()
        executor = RouteRecorder(raises=InvalidParamsError(message="refused"))

        with pytest.raises(InvalidParamsError, match="refused"):
            await handler(spy, executor=executor)._setup_active_task(params(), call_context())

        assert len(executor.admitted) == 1
        assert isinstance(executor.admitted[0], AionRuntimeExtensions)
        assert spy.calls == []
        assert accepting_super == []

    async def test_a_message_continuing_a_task_keeps_its_route(self, accepting_super):
        """Only a message that starts a task has its route decided; its task's handler checks the message."""
        executor = RouteRecorder()
        request = params()
        request.message.task_id = "task-1"

        await handler(executor=executor)._setup_active_task(request, call_context())

        assert executor.admitted == []
        assert [task.id for task in executor.continued] == ["task-1"]
        assert accepting_super == ["setup"]

    async def test_a_message_the_builder_hands_to_an_interrupted_task_keeps_its_route(
        self, accepting_super
    ):
        """A message without a task id that continues the addressed handler's waiting task names that task."""
        executor = RouteRecorder()
        instance = handler(executor=executor)
        asked = []

        class Builder:
            async def interrupted_task(self, context_id, call_context, routed_extension):
                asked.append(routed_extension)
                return Task(id="task-9", context_id=context_id) if context_id == "ctx-1" else None

        instance._request_context_builder = Builder()
        request = params()
        request.message.context_id = "ctx-1"

        await instance._setup_active_task(request, call_context())

        assert asked == [RouteRecorder.URI]
        assert executor.admitted == []
        assert request.message.task_id == "task-9"
        assert [task.id for task in executor.continued] == ["task-9"]

    async def test_a_refused_continuation_reaches_no_preprocessor_and_no_task(self, accepting_super):
        """The task's handler refusing the message leaves the task and the request untouched."""
        spy = SpyPreprocessor()
        executor = RouteRecorder()

        def refuse(task) -> None:
            raise InvalidParamsError(message="extension unavailable")

        executor.admit_continuation = refuse
        request = params()
        request.message.task_id = "task-1"

        with pytest.raises(InvalidParamsError, match="extension unavailable"):
            await handler(spy, executor=executor)._setup_active_task(request, call_context())

        assert spy.calls == []
        assert accepting_super == []

    async def test_admission_into_the_context_runs_before_anything_else(self, accepting_super):
        """A caller refused the context must not reach verification, preprocessing or setup."""
        spy = SpyPreprocessor()
        refusing = handler(spy)

        async def refuse(params, call_context) -> None:
            raise TaskNotFoundError()

        refusing._admit = refuse
        with pytest.raises(TaskNotFoundError):
            await refusing._setup_active_task(params(), call_context())

        assert spy.calls == []
        assert accepting_super == []

    async def test_preprocessing_runs_before_task_setup(self, accepting_super):
        spy = SpyPreprocessor()
        await handler(spy)._setup_active_task(params(), call_context())

        assert len(spy.calls) == 1
        assert accepting_super == ["setup"]

    async def test_preprocessor_receives_the_verified_projection(self, accepting_super):
        """Not the raw request: the projection is what verification produced."""
        aion_a2a_extension_registry.reset_to_default()
        spy = SpyPreprocessor()

        await handler(spy)._setup_active_task(params(), call_context())

        context = spy.calls[0]
        assert isinstance(context.extensions, AionRuntimeExtensions)

    async def test_header_carried_extensions_reach_the_projection(
        self, accepting_super
    ):
        """Verification must see call_context or every header extension fails here.

        HeaderCollector reads its value from ``call_context.state["headers"]``.
        Without the call context in the declaration, usage attribution - the
        carrier that decides who is billed for a stored file - would be absent
        from the projection on every request.
        """
        from aion.core.constants import (
            AION_USAGE_ATTRIBUTION_HEADER,
            USAGE_ATTRIBUTION_EXTENSION_URI_V1,
        )

        aion_a2a_extension_registry.reset_to_default()
        spy = SpyPreprocessor()
        request = params()
        request.message.extensions.append(USAGE_ATTRIBUTION_EXTENSION_URI_V1)
        context = SimpleNamespace(
            requested_extensions=set(),
            state={"headers": {AION_USAGE_ATTRIBUTION_HEADER: "carrier-1"}},
        )

        await handler(spy)._setup_active_task(request, context)

        extensions = spy.calls[0].extensions
        assert extensions.get(USAGE_ATTRIBUTION_EXTENSION_URI_V1) == "carrier-1"


class TestCompensation:
    async def test_a_failing_preprocessor_is_rolled_back_itself(self, accepting_super):
        """Its batch can be half-done, and only its own rollback knows about it."""
        failing = SpyPreprocessor("failing", raises=InvalidParamsError(message="no"))

        with pytest.raises(InvalidParamsError):
            await handler(failing)._setup_active_task(params(), call_context())

        assert failing.rollbacks == 1

    async def test_earlier_preprocessors_are_rolled_back_in_reverse(
        self, accepting_super
    ):
        order: list[str] = []

        class Ordered(SpyPreprocessor):
            async def rollback(self):
                order.append(self.name)

        first = Ordered("first")
        second = Ordered("second", raises=InvalidParamsError(message="no"))
        third = Ordered("third")

        with pytest.raises(InvalidParamsError):
            await handler(first, second, third)._setup_active_task(
                params(), call_context()
            )

        assert order == ["second", "first"]
        assert third.calls == []

    async def test_downstream_failure_rolls_everything_back(self, failing_super):
        first = SpyPreprocessor("first")
        second = SpyPreprocessor("second")

        with pytest.raises(RuntimeError, match="downstream boom"):
            await handler(first, second)._setup_active_task(params(), call_context())

        assert (first.rollbacks, second.rollbacks) == (1, 1)

    async def test_a_failing_rollback_does_not_mask_the_original_error(
        self, failing_super
    ):
        class BrokenRollback(SpyPreprocessor):
            async def rollback(self):
                raise RuntimeError("rollback boom")

        broken = BrokenRollback("broken")
        other = SpyPreprocessor("other")

        with pytest.raises(RuntimeError, match="downstream boom"):
            await handler(broken, other)._setup_active_task(params(), call_context())

        assert other.rollbacks == 1
