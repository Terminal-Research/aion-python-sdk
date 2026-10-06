"""Attribution follows the accepted invocation, not a task or a process global."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState, TaskStatus
from a2a.utils.errors import InternalError, InvalidParamsError

from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER, USAGE_ATTRIBUTION_EXTENSION_URI_V1
from aion.core.exceptions import AionAuthenticationError
from aion.core.principal import Principal
from aion.core.runtime.context import (
    AionRuntimeContext, AionRuntimeContextRegistry, AionRuntimeExtensions,
    DirectAttribution, ForwardedAttribution, get_aion_runtime_context,
)
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.context.attribution import callback_attribution
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.scope import init_execution_scope, clear_execution_scope
from aion.server.auth import Assurance, AuthenticatedCaller, CallerCredentials, CredentialKind


@pytest.fixture(autouse=True)
def scope():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    init_execution_scope()
    yield
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()
    clear_execution_scope()


def request(number=1, *, carrier=None, resume=False):
    principal = Principal("AnonymousSession", f"00000000-0000-0000-0000-{number:012d}")
    caller = AuthenticatedCaller(principal, credential=CredentialKind.SESSION,
        assurance=Assurance.SESSION, issuer="aion.io", claims={})
    call = ServerCallContext(state={"auth": CallerCredentials(caller)})
    if carrier is not None:
        call.state["headers"] = {AION_USAGE_ATTRIBUTION_HEADER: carrier}
        call.requested_extensions = {USAGE_ATTRIBUTION_EXTENSION_URI_V1}
    message = Message(message_id=f"message-{number}", context_id=f"context-{number}",
        role=Role.ROLE_USER, parts=[Part(text="continue")])
    task = Task(id=f"task-{number}", context_id=message.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED)) if resume else None
    return RequestContext(call_context=call,
        request=SendMessageRequest(message=message), task=task), principal


def executor(stream):
    return AionAgentRequestExecutor(SimpleNamespace(stream=stream, resume=stream))


@pytest.mark.parametrize("resume", [False, True])
async def test_concurrent_streams_keep_each_caller_and_restore_parent(resume):
    parent = AionRuntimeContext(callback_attribution=DirectAttribution(Principal("AionUser", "parent")))
    AionRuntimeContextRegistry.set_current_context(parent)
    entered = asyncio.Event()
    seen = []

    async def stream(context):
        current = get_aion_runtime_context()
        expected = context.call_context.state["auth"].caller.principal
        assert current.get_callback_attribution() == DirectAttribution(expected)
        seen.append(expected)
        if len(seen) == 2:
            entered.set()
        await entered.wait()
        assert get_aion_runtime_context() is current
        assert current.graph_kwargs == {}
        if False:
            yield

    worker = executor(stream)
    await asyncio.wait_for(asyncio.gather(*[
        worker.execute(request(number, resume=resume)[0], AsyncMock())
        for number in (1, 2)
    ]), timeout=2)
    assert len(set(seen)) == 2
    assert get_aion_runtime_context() is parent


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_nested_execution_restores_context_even_on_failure(error):
    async def inner(context):
        assert get_aion_runtime_context().get_callback_attribution() == DirectAttribution(request(2)[1])
        raise error()
        yield

    async def outer(context):
        current = get_aion_runtime_context()
        with pytest.raises(InternalError if error is RuntimeError else error):
            await executor(inner).execute(request(2)[0], AsyncMock())
        assert get_aion_runtime_context() is current
        if False:
            yield

    await executor(outer).execute(request()[0], AsyncMock())
    assert get_aion_runtime_context() is None


async def test_background_work_inherits_only_its_explicit_execution_scope():
    pending = []
    release = asyncio.Event()

    async def work(expected):
        await release.wait()
        assert get_aion_runtime_context().get_callback_attribution() == expected

    async def stream(context):
        pending.append(asyncio.create_task(work(get_aion_runtime_context().get_callback_attribution())))
        if False:
            yield

    await executor(stream).execute(request()[0], AsyncMock())
    assert get_aion_runtime_context() is None
    release.set()
    await asyncio.gather(*pending)


async def test_cancel_receives_current_request_attribution():
    context, caller = request(resume=True)
    async def cancel(**kwargs):
        assert get_aion_runtime_context().get_callback_attribution() == DirectAttribution(caller)
    worker = AionAgentRequestExecutor(SimpleNamespace(cancel=cancel))
    await worker.cancel(context, AsyncMock())
    assert get_aion_runtime_context() is None


async def test_opaque_carrier_is_not_replaced_by_session_or_old_checkpoint():
    async def stream(context):
        assert get_aion_runtime_context().get_callback_attribution() == ForwardedAttribution("opaque-carrier")
        if False:
            yield
    await executor(stream).execute(request(carrier="opaque-carrier", resume=True)[0], AsyncMock())
    assert get_aion_runtime_context() is None


@pytest.mark.parametrize("activated", [True, False])
async def test_missing_or_unactivated_carrier_cannot_fall_back(activated):
    context, _ = request(carrier="")
    if not activated:
        context.call_context.requested_extensions.clear()
    worker = executor(AsyncMock())
    queue = AsyncMock()
    with pytest.raises(InvalidParamsError):
        await worker.execute(context, queue)
    queue.enqueue_event.assert_not_called()
    assert get_aion_runtime_context() is None


def test_absent_canonical_caller_is_external_anonymous_not_a_guessed_user():
    context = SimpleNamespace(call_context=SimpleNamespace(
        state={}, user=SimpleNamespace(user_name="raw-provider-name", is_authenticated=True)))
    assert callback_attribution(context.call_context, AionRuntimeContext()) == DirectAttribution(
        Principal("ExternalAnonymous", "external-anonymous"))


def test_distribution_payload_does_not_override_verified_caller():
    context, principal = request()
    runtime = AionRuntimeContext(distribution_extension_payload=SimpleNamespace(caller_id="spoofed"))
    assert callback_attribution(context.call_context, runtime) == DirectAttribution(principal)


def test_conflicting_runtime_inputs_are_rejected():
    context = AionRuntimeContext(
        extensions=AionRuntimeExtensions({USAGE_ATTRIBUTION_EXTENSION_URI_V1: "signed"}),
        callback_attribution=DirectAttribution(request()[1]),
    )
    with pytest.raises(AionAuthenticationError, match="Conflicting"):
        context.get_callback_attribution()
