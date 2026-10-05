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
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        message = Message(message_id="m-1", role=Role.ROLE_USER)
        message.extensions.append(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
        params = SendMessageRequest(message=message)

        with pytest.raises(InvalidParamsError, match="not enabled for this agent"):
            self._verify(params, self._call_context())

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

            with pytest.raises(InvalidParamsError, match="daemon handler cannot run here"):
                self._verify(params, self._call_context())

            # Sanity: once availability is restored the same request passes.
            aion_a2a_extension_registry.reset_to_default()
            aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
            self._verify(params, self._call_context())
        finally:
            aion_a2a_extension_registry.reset_to_default()
