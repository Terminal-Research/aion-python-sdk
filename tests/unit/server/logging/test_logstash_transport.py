"""Tests for AionLogstashTransport.

Focus areas:
  Local mode (use_platform_auth=False):
    - Sends events without Authorization header
    - Uses plain HTTP semantics inherited from HttpTransport

  Remote mode (use_platform_auth=True):
    - Attaches Bearer token from the Aion platform JWT manager
    - Drops events without any HTTP call when no token is available
    - Emits the "delivery skipped" warning only once
    - Resumes delivery and logs recovery once a token becomes available

  Handler wiring:
    - AionLogstashHandler forwards use_platform_auth to the transport
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from aion.server.logging.handlers.logstash import (
    AionLogstashHandler,
    AionLogstashTransport,
)


def _make_transport(use_platform_auth: bool) -> AionLogstashTransport:
    return AionLogstashTransport(
        host="localhost",
        port=8081,
        timeout=5.0,
        ssl_enable=use_platform_auth,
        ssl_verify=use_platform_auth,
        use_platform_auth=use_platform_auth,
    )


def _mock_session():
    """Return a mocked requests.Session instance with a 200 response."""
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200)
    return session


EVENTS = ['{"message": "event-1"}', '{"message": "event-2"}']


class TestLocalMode:
    def test_sends_without_authorization_header(self):
        transport = _make_transport(use_platform_auth=False)
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ):
            transport.send(EVENTS)

        session.post.assert_called_once()
        headers = session.post.call_args.kwargs["headers"]
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_url_is_plain_http(self):
        transport = _make_transport(use_platform_auth=False)
        assert transport.url.startswith("http://")

    def test_does_not_touch_jwt_manager(self):
        transport = _make_transport(use_platform_auth=False)
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ), patch.object(
            AionLogstashTransport, "_get_platform_token"
        ) as get_token:
            transport.send(EVENTS)

        get_token.assert_not_called()


class TestRemoteMode:
    def test_sends_with_bearer_token(self):
        transport = _make_transport(use_platform_auth=True)
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ), patch.object(
            AionLogstashTransport, "_get_platform_token", return_value="test-token"
        ):
            transport.send(EVENTS)

        session.post.assert_called_once()
        headers = session.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-token"

    def test_url_is_https(self):
        transport = _make_transport(use_platform_auth=True)
        assert transport.url.startswith("https://")

    def test_drops_events_without_token(self, caplog):
        transport = _make_transport(use_platform_auth=True)
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ), patch.object(
            AionLogstashTransport, "_get_platform_token", return_value=None
        ), caplog.at_level(logging.WARNING):
            transport.send(EVENTS)

        session.post.assert_not_called()
        assert any(
            "requires an Aion platform token" in rec.message for rec in caplog.records
        )

    def test_warns_only_once_while_token_missing(self, caplog):
        transport = _make_transport(use_platform_auth=True)

        with patch.object(
            AionLogstashTransport, "_get_platform_token", return_value=None
        ), caplog.at_level(logging.WARNING):
            transport.send(EVENTS)
            transport.send(EVENTS)
            transport.send(EVENTS)

        warnings = [
            rec for rec in caplog.records
            if "requires an Aion platform token" in rec.message
        ]
        assert len(warnings) == 1

    def test_resumes_delivery_after_token_recovery(self, caplog):
        transport = _make_transport(use_platform_auth=True)
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ), caplog.at_level(logging.INFO):
            with patch.object(
                AionLogstashTransport, "_get_platform_token", return_value=None
            ):
                transport.send(EVENTS)

            with patch.object(
                AionLogstashTransport, "_get_platform_token", return_value="token"
            ):
                transport.send(EVENTS)

        session.post.assert_called_once()
        assert any("resumed" in rec.message for rec in caplog.records)
        # Warning flag is reset so a new outage warns again
        assert transport._token_warning_emitted is False


class TestUrl:
    """The endpoint resolver always supplies a concrete scheme, port and path,
    so the transport only has to address what it is given."""

    def test_local_url(self):
        transport = _make_transport(use_platform_auth=False)
        assert transport.url == "http://localhost:8081/"

    def test_platform_url_with_path(self):
        transport = AionLogstashTransport(
            host="logs.aion.to",
            port=443,
            path="ingest",
            ssl_enable=True,
            use_platform_auth=True,
        )
        assert transport.url == "https://logs.aion.to:443/ingest"

    def test_posts_to_resolved_url(self):
        transport = AionLogstashTransport(
            host="logs.aion.to",
            port=443,
            path="ingest",
            ssl_enable=True,
            use_platform_auth=True,
        )
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ), patch.object(
            AionLogstashTransport, "_get_platform_token", return_value="test-token"
        ):
            transport.send(EVENTS)

        assert session.post.call_args.args[0] == "https://logs.aion.to:443/ingest"


class TestBatching:
    def test_splits_oversized_payload_into_batches(self):
        transport = _make_transport(use_platform_auth=False)
        transport._max_content_length = 60
        session = _mock_session()

        events = [
            '{"message": "batch-a"}',
            '{"message": "batch-b"}',
            '{"message": "batch-c"}',
        ]
        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ):
            transport.send(events)

        assert session.post.call_count > 1
        sent = [call.kwargs["json"] for call in session.post.call_args_list]
        assert [event for batch in sent for event in batch] == [
            {"message": "batch-a"},
            {"message": "batch-b"},
            {"message": "batch-c"},
        ]

    def test_skips_single_event_larger_than_limit(self):
        transport = _make_transport(use_platform_auth=False)
        transport._max_content_length = 10
        session = _mock_session()

        with patch(
            "aion.server.logging.handlers.logstash.transport.requests.Session",
            return_value=session,
        ):
            transport.send(['{"message": "way-too-large-event"}'])

        session.post.assert_not_called()


class TestHandlerWiring:
    def test_handler_builds_transport_with_platform_auth(self):
        handler = AionLogstashHandler(
            host="localhost",
            port=8081,
            database_path=None,
            transport=AionLogstashTransport,
            ssl_enable=True,
            ssl_verify=True,
            use_platform_auth=True,
            enable=True,
            client_id="client-id",
            node_name="node",
        )

        assert isinstance(handler._transport, AionLogstashTransport)
        assert handler._transport._use_platform_auth is True
        assert handler._transport.url.startswith("https://")

    def test_handler_forwards_path(self):
        handler = AionLogstashHandler(
            host="logs.aion.to",
            port=443,
            path="ingest",
            database_path=None,
            transport=AionLogstashTransport,
            ssl_enable=True,
            ssl_verify=True,
            use_platform_auth=True,
            enable=True,
            client_id="client-id",
            node_name="node",
        )

        assert handler._transport.url == "https://logs.aion.to:443/ingest"

    def test_handler_builds_transport_local_mode(self):
        handler = AionLogstashHandler(
            host="localhost",
            port=8081,
            database_path=None,
            transport=AionLogstashTransport,
            ssl_enable=False,
            ssl_verify=False,
            use_platform_auth=False,
            enable=True,
            client_id="client-id",
            node_name="node",
        )

        assert isinstance(handler._transport, AionLogstashTransport)
        assert handler._transport._use_platform_auth is False
        assert handler._transport.url.startswith("http://")
