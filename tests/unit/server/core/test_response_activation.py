"""Confirmed headers precede lazy SSE and do not echo unverified requests."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import SendMessageRequest, Task, TaskStatus, TaskState
from a2a.utils.errors import InvalidParamsError
from google.protobuf.json_format import ParseDict

from aion.core.constants.a2a import CRON_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.core.app.handlers.jsonrpc_dispatcher import AionJsonRpcDispatcher
from aion.server.core.app.handlers.request_handler import AionRequestHandler


@pytest.fixture(autouse=True)
def registry():
    aion_a2a_extension_registry.reset_to_default()
    yield
    aion_a2a_extension_registry.reset_to_default()


def request(activate=True):
    """Use the same wire fixture as Scala rather than constructing a new contract."""
    payload = json.loads(
        (Path(__file__).parents[2] / 'core/fixtures/a2a/cron-recurring.json').read_text()
    )
    return ParseDict({
        'message': {'messageId': 'input', 'role': 'ROLE_USER', 'parts': [{'text': 'hello'}]},
        'metadata': {CRON_EXTENSION_URI_V1: payload} if activate else {},
    }, SendMessageRequest())


def context():
    return ServerCallContext(
        state={'method': 'SendStreamingMessage', 'headers': {'A2A-Version': '1.0'}},
        requested_extensions={'urn:unknown'},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['empty', 'delayed', 'error'])
async def test_head_available_without_pulling_event_and_stream_closes(mode):
    call = context()
    started = asyncio.Event()
    closed = asyncio.Event()
    release = asyncio.Event()

    async def events(params, call_context):
        started.set()
        try:
            if mode == 'delayed':
                await release.wait()
            if mode == 'error':
                raise InvalidParamsError(message='execution rejected')
            if mode != 'empty':
                yield Task(id='task', context_id='context', status=TaskStatus(
                    state=TaskState.TASK_STATE_COMPLETED))
        finally:
            closed.set()

    handler = Mock(spec=AionRequestHandler)
    handler.verify_declared_extensions.side_effect = lambda params, ctx: (
        AionRequestHandler.verify_declared_extensions(None, params, ctx)
    )
    handler.on_message_send_stream = events
    dispatcher = AionJsonRpcDispatcher(handler)
    body = await dispatcher._process_streaming_request(1, request(), call)
    response = dispatcher._create_response(call, body)
    assert response.headers['A2A-Extensions'] == CRON_EXTENSION_URI_V1
    assert not started.is_set()
    first = asyncio.create_task(anext(body, None))
    await started.wait()
    if mode == 'delayed':
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    else:
        result = await first
        assert result is None if mode == 'empty' else 'error' in result
    await body.aclose()
    assert closed.is_set()


@pytest.mark.parametrize('activate', [True, False])
def test_unary_header_uses_verification_not_requested_or_advertised_support(activate):
    call = context()
    AionRequestHandler.verify_declared_extensions(None, request(activate), call)
    response = AionJsonRpcDispatcher(Mock())._create_response(call, {'error': {'code': -1}})
    assert response.headers.get('A2A-Extensions') == (CRON_EXTENSION_URI_V1 if activate else None)


def test_invalid_activation_has_no_confirmed_head():
    call = context()
    call.requested_extensions.add(CRON_EXTENSION_URI_V1)
    with pytest.raises(InvalidParamsError):
        AionRequestHandler.verify_declared_extensions(None, request(False), call)
    assert not ResponseServiceParameters.from_context(call).activated_extensions


def test_read_does_not_acknowledge_historical_extensions():
    call = context()
    call.state['method'] = 'GetTask'
    response = AionJsonRpcDispatcher(Mock())._create_response(call, {
        'result': {'history': [{'extensions': [CRON_EXTENSION_URI_V1]}]}
    })
    assert 'A2A-Extensions' not in response.headers
