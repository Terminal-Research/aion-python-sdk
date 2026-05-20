from unittest.mock import MagicMock, Mock


def make_mock_inbox(message=None, task=None):
    inbox = Mock()
    inbox.message = message
    inbox.task = task
    return inbox


def make_mock_identity(network_type="A2A"):
    identity = Mock()
    identity.network_type = network_type
    return identity


def make_mock_event(kind=None, payload=None):
    event = Mock()
    event.kind = kind
    event.payload = payload
    return event


def make_mock_context(event=None, identity=None, inbox=None):
    ctx = Mock()
    ctx.event = event
    ctx.identity = identity if identity is not None else make_mock_identity()
    ctx.inbox = inbox if inbox is not None else make_mock_inbox()
    return ctx


def make_mock_runtime(context=None):
    runtime = Mock()
    runtime.context = context
    return runtime
