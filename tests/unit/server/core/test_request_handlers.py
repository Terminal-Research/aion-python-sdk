import pytest
from unittest.mock import AsyncMock, Mock

from aion.core.a2a import DeleteContextParams, GetContextParams, GetContextsParams
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.admission import InMemoryContextAdmission
from aion.server.tasks.contexts import InMemoryContextCatalog
from aion.server.tasks.stores import InMemoryTaskStore


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _handler(**kwargs) -> AionRequestHandler:
    return AionRequestHandler(
        agent_executor=Mock(),
        task_store=kwargs.pop("task_store", InMemoryTaskStore()),
        agent_card=Mock(),
        **kwargs,
    )


class TestContextMethods:
    """The handler answers the Context extension through its ContextService."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "handler_name,service_name,params",
        [
            ("on_get_contexts", "get_contexts", GetContextsParams()),
            ("on_get_context", "get_context", GetContextParams(context_id="c1")),
            ("on_delete_context", "delete_context", DeleteContextParams(context_id="c1")),
        ],
    )
    async def test_each_method_delegates_with_the_call_context(self, handler_name, service_name, params):
        handler = _handler()
        service = Mock()
        setattr(service, service_name, AsyncMock(return_value="answer"))
        handler._contexts = service
        call_context = object()

        assert await getattr(handler, handler_name)(params, call_context) == "answer"
        getattr(service, service_name).assert_awaited_once_with(params, call_context)

    def test_admission_and_catalog_are_chosen_together(self):
        """A catalog over other reservations than the ones admission writes would read nothing."""
        store = InMemoryTaskStore()
        admission = InMemoryContextAdmission()
        with pytest.raises(ValueError):
            _handler(task_store=store, admission=admission)
        with pytest.raises(ValueError):
            _handler(task_store=store, context_catalog=InMemoryContextCatalog(store, admission))

    def test_both_may_be_passed(self):
        store = InMemoryTaskStore()
        admission = InMemoryContextAdmission()
        handler = _handler(
            task_store=store, admission=admission, context_catalog=InMemoryContextCatalog(store, admission)
        )
        assert handler._admission is admission


class TestVerifyDeclaredExtensions:
    """Extension declarations are verified from the request path, before any
    ActiveTask machinery: client mistakes must come back as InvalidParamsError
    (a WARNING-level request error), not as producer 'Execution failed'
    ERROR tracebacks."""

    @staticmethod
    def _call_context(requested=frozenset()):
        from types import SimpleNamespace
        return SimpleNamespace(requested_extensions=set(requested), state={})

    @staticmethod
    def _handler_self():
        """Minimal stand-in for the handler instance (self is not consulted)."""
        from types import SimpleNamespace
        return SimpleNamespace()

    def _verify(self, params, call_context):
        AionRequestHandler.verify_declared_extensions(
            self._handler_self(), params, call_context
        )

    def test_declared_inactive_extension_rejected_as_invalid_params(self):
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        message = Message(message_id="m-1", role=Role.ROLE_USER)
        message.extensions.append(DAEMON_EXTENSION_URI_V1)
        params = SendMessageRequest(message=message)

        with pytest.raises(InvalidParamsError, match="not enabled for this agent") as refusal:
            self._verify(params, self._call_context())
        assert refusal.value.data is None

    def test_header_declared_inactive_extension_rejected(self):
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))

        with pytest.raises(InvalidParamsError):
            self._verify(params, self._call_context({DAEMON_EXTENSION_URI_V1}))

    def test_request_without_declared_extensions_passes(self):
        from a2a.types import Message, Role, SendMessageRequest
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))

        result = self._verify(params, self._call_context())
        assert result is None

    def test_a_coded_refusal_carries_its_own_retryable(self):
        """A collector's refusal with a code of its own travels with the ``retryable`` it gives."""
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.runtime import ExtensionActivationError, aion_a2a_extension_registry
        from aion.core.runtime.context.extensions import ExtensionDescriptor

        uri = "https://example.invalid/coded/1.0.0"

        class Refusing:
            def collect(self, uri, request_context):
                raise ExtensionActivationError(uri, reason="bad payload", code="invalid_payload", retryable=False)

        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=uri, collector=Refusing()))
        try:
            message = Message(message_id="m-1", role=Role.ROLE_USER)
            message.extensions.append(uri)

            with pytest.raises(InvalidParamsError, match="bad payload") as refusal:
                self._verify(SendMessageRequest(message=message), self._call_context())
            assert refusal.value.data == {"code": "invalid_payload", "retryable": "false"}
        finally:
            aion_a2a_extension_registry.reset_to_default()

    def test_enabled_extension_marked_unavailable_rejected(self):
        """The silent-fallback guard: an enabled extension marked unavailable
        in the registry (e.g. its toolkit is not installed) must reject the
        request with the recorded reason, not fall through to the primary graph."""
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry
        from google.protobuf.json_format import ParseDict

        aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
        aion_a2a_extension_registry.mark_unavailable(
            DAEMON_EXTENSION_URI_V1, "the daemon handler cannot run here"
        )
        try:
            params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))
            ParseDict({
                "daemonIdentity": {
                    "kind": "daemon",
                    "id": "daemon-1",
                    "networkType": "Aion",
                    "organizationId": "org-1",
                },
                "behavior": {"id": "b-1", "behaviorKey": "main", "versionId": "v-1"},
                "environment": {
                    "id": "env-1",
                    "name": "dev",
                    "projectId": "proj-1",
                    "deploymentId": "dep-1",
                    "configurationVariables": {},
                    "daemonAgentIdentityId": "daemon-1",
                },
            }, params.metadata.get_or_create_struct(DAEMON_EXTENSION_URI_V1))

            with pytest.raises(InvalidParamsError, match="daemon handler cannot run here") as refusal:
                self._verify(params, self._call_context())
            assert refusal.value.data == {"code": "extension_unavailable", "retryable": "true"}

            # Sanity: once availability is restored the same request passes.
            aion_a2a_extension_registry.reset_to_default()
            aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
            self._verify(params, self._call_context())
        finally:
            aion_a2a_extension_registry.reset_to_default()


class TestAbandonedNamedTask:
    """A message naming an abandoned task settles it first, unless its turn runs on this server."""

    @staticmethod
    def _handler(*, running_here: bool, settled):
        from types import SimpleNamespace

        instance = AionRequestHandler.__new__(AionRequestHandler)
        instance._abandoned_tasks = SimpleNamespace(settle=AsyncMock(return_value=settled))
        instance._active_task_registry = SimpleNamespace(
            get=AsyncMock(return_value=object() if running_here else None)
        )
        return instance

    @staticmethod
    def _task(state):
        from a2a.types import Task, TaskStatus

        return Task(id="t-1", context_id="ctx-1", status=TaskStatus(state=state))

    @pytest.mark.anyio
    async def test_an_abandoned_task_is_settled_before_the_message_reaches_it(self):
        from a2a.types import TaskState

        settled = self._task(TaskState.TASK_STATE_FAILED)
        handler = self._handler(running_here=False, settled=settled)

        assert await handler._settle_if_abandoned(self._task(TaskState.TASK_STATE_WORKING)) is settled
        handler._abandoned_tasks.settle.assert_awaited_once_with("t-1")

    @pytest.mark.anyio
    async def test_a_task_that_is_not_abandoned_is_left_as_it_is(self):
        from a2a.types import TaskState

        handler = self._handler(running_here=False, settled=None)
        working = self._task(TaskState.TASK_STATE_WORKING)

        assert await handler._settle_if_abandoned(working) is working

    @pytest.mark.anyio
    @pytest.mark.parametrize("running_here", [True, False])
    async def test_a_task_running_here_or_waiting_for_input_is_not_settled(self, running_here):
        from a2a.types import TaskState

        handler = self._handler(running_here=running_here, settled=None)
        state = TaskState.TASK_STATE_WORKING if running_here else TaskState.TASK_STATE_INPUT_REQUIRED
        named = self._task(state)

        assert await handler._settle_if_abandoned(named) is named
        handler._abandoned_tasks.settle.assert_not_awaited()


class TestWorkingFileLinks:
    """Whatever the handler answers with leaves through ``file_links``, so a stored file's link works."""

    CARRY_CONTENT = {
        "on_get_task",
        "on_list_tasks",
        "on_message_send",
        "on_message_send_stream",
        "on_cancel_task",
        "on_subscribe_to_task",
        "on_get_context",
    }
    """The answers that carry a task, a message or an event."""

    CARRY_NO_FILE = {
        "on_get_contexts",
        "on_delete_context",
        "on_create_task_push_notification_config",
        "on_get_task_push_notification_config",
        "on_list_task_push_notification_configs",
        "on_delete_task_push_notification_config",
        "on_get_extended_agent_card",
    }
    """The answers that carry no part: summaries, configs, a card, a deletion's result."""

    def test_every_answer_is_accounted_for(self):
        """A method added to the handler is decided here: does its answer carry files?"""
        answers = {name for name in dir(AionRequestHandler) if name.startswith("on_")}

        assert answers == self.CARRY_CONTENT | self.CARRY_NO_FILE

    @pytest.mark.parametrize("name", sorted(CARRY_CONTENT))
    def test_an_answer_with_content_refreshes_its_links(self, name):
        import inspect

        source = inspect.getsource(getattr(AionRequestHandler, name))

        assert any(
            call in source
            for call in ("_with_working_links(", "_context_with_working_links(", "refresh_many(")
        )

    @staticmethod
    def _links():
        links = Mock()
        links.refresh = AsyncMock(side_effect=lambda content: ("fresh", content))
        return links

    @pytest.mark.anyio
    async def test_a_task_is_answered_with_working_links(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2

        monkeypatch.setattr(DefaultRequestHandlerV2, "on_get_task", AsyncMock(return_value="stored task"))
        handler = _handler(file_links=self._links())

        assert await handler.on_get_task(Mock(id="t1"), None) == ("fresh", "stored task")

    @pytest.mark.anyio
    async def test_a_missing_task_is_answered_as_missing(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2

        monkeypatch.setattr(DefaultRequestHandlerV2, "on_get_task", AsyncMock(return_value=None))
        links = self._links()
        handler = _handler(file_links=links)

        assert await handler.on_get_task(Mock(id="t1"), None) is None
        links.refresh.assert_not_awaited()

    @pytest.mark.anyio
    async def test_without_file_links_an_answer_is_the_stored_task(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2

        monkeypatch.setattr(DefaultRequestHandlerV2, "on_get_task", AsyncMock(return_value="stored task"))

        assert await _handler().on_get_task(Mock(id="t1"), None) == "stored task"

    @pytest.mark.anyio
    async def test_a_list_is_answered_with_the_refreshed_tasks(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2
        from a2a.types import ListTasksResponse, Task
        from aion.server.core.app.handlers import request_handler

        stored = ListTasksResponse(tasks=[Task(id="t1"), Task(id="t2")], total_size=2)
        monkeypatch.setattr(DefaultRequestHandlerV2, "on_list_tasks", AsyncMock(return_value=stored))
        monkeypatch.setattr(request_handler, "has_individual_access", lambda context: True)
        links = Mock()
        links.refresh_many = AsyncMock(side_effect=lambda tasks: [tasks[0], Task(id="t2", context_id="refreshed")])
        handler = _handler(file_links=links)

        answer = await handler.on_list_tasks(Mock(), None)

        assert [task.context_id for task in answer.tasks] == ["", "refreshed"]
        assert answer.total_size == 2
        assert [task.context_id for task in stored.tasks] == ["", ""]

    @pytest.mark.anyio
    async def test_a_list_with_nothing_to_refresh_is_answered_as_stored(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2
        from a2a.types import ListTasksResponse, Task
        from aion.server.core.app.handlers import request_handler

        stored = ListTasksResponse(tasks=[Task(id="t1")])
        monkeypatch.setattr(DefaultRequestHandlerV2, "on_list_tasks", AsyncMock(return_value=stored))
        monkeypatch.setattr(request_handler, "has_individual_access", lambda context: True)
        links = Mock()
        links.refresh_many = AsyncMock(side_effect=lambda tasks: list(tasks))

        assert await _handler(file_links=links).on_list_tasks(Mock(), None) is stored

    @pytest.mark.anyio
    async def test_every_stream_event_leaves_with_working_links(self, monkeypatch):
        from a2a.server.request_handlers.default_request_handler_v2 import DefaultRequestHandlerV2
        from aion.server.core.app.handlers import request_handler

        async def events(self, params, context):
            yield "first"
            yield "second"

        class PassThrough:
            def __init__(self, **kwargs):
                pass

            def project(self, source):
                return source

        monkeypatch.setattr(DefaultRequestHandlerV2, "on_message_send_stream", events)
        monkeypatch.setattr(request_handler, "TerminalTaskProjection", PassThrough)
        handler = _handler(file_links=self._links())

        sent = [event async for event in handler.on_message_send_stream(Mock(), None)]

        assert sent == [("fresh", "first"), ("fresh", "second")]

    @pytest.mark.anyio
    async def test_a_context_is_answered_with_working_links(self):
        from datetime import datetime, timezone

        from a2a.types import Artifact, Message, Part, Role, TaskState, TaskStatus
        from aion.core.a2a import ContextArtifact, ContextView

        def with_part(url):
            return Part(url=url, filename="a.pdf")

        view = ContextView(
            context_id="c1",
            history=[Message(message_id="m1", role=Role.ROLE_USER, parts=[with_part("old-1")])],
            artifacts=[ContextArtifact(task_id="t1", artifact=Artifact(artifact_id="a1", parts=[with_part("old-2")]))],
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            last_activity_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        )

        def renewed(contents):
            fresh = []
            for content in contents:
                if getattr(content, "parts", None):
                    content = type(content).FromString(content.SerializeToString())
                    content.parts[0].url = content.parts[0].url.replace("old", "new")
                fresh.append(content)
            return fresh

        links = Mock()
        links.refresh_many = AsyncMock(side_effect=renewed)
        handler = _handler(file_links=links)
        handler._contexts = Mock(get_context=AsyncMock(return_value=view))

        answer = await handler.on_get_context(GetContextParams(context_id="c1"), None)

        assert answer.history[0].parts[0].url == "new-1"
        assert answer.artifacts[0].artifact.parts[0].url == "new-2" and answer.artifacts[0].task_id == "t1"
        assert view.history[0].parts[0].url == "old-1" and view.artifacts[0].artifact.parts[0].url == "old-2"
