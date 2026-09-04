"""HTTP transport delivering log batches, optionally authenticated with Aion."""

import json
import logging
from typing import Optional

import requests
from logstash_async.transport import HttpTransport
from requests.auth import HTTPBasicAuth

__all__ = ["AionLogstashTransport"]

logger = logging.getLogger(__name__)


class AionLogstashTransport(HttpTransport):
    """HTTP transport with optional Aion platform authentication.

    When ``use_platform_auth`` is set, every batch is sent with an
    ``Authorization: Bearer`` header using a token obtained from the Aion
    platform via the shared JWT manager. While no token can be obtained,
    batches are dropped silently (a single warning is emitted) so that
    server logs are not flooded with delivery errors.

    Args:
        use_platform_auth: Require an Aion platform token for every request.
        **kwargs: Arguments passed to HttpTransport (host, port, ssl_enable, ...).
    """

    def __init__(self, *args, use_platform_auth: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._use_platform_auth = use_platform_auth
        self._token_warning_emitted = False

    def send(self, events: list, **kwargs) -> None:
        """Send events to the Logstash pipeline.

        Mirrors :meth:`HttpTransport.send`, but attaches an Aion platform
        Bearer token when platform auth is enabled and skips delivery
        entirely when the token is not available.

        Args:
            events: A list of already formatted (JSON string) events.
        """
        headers = {'Content-Type': 'application/json'}

        if self._use_platform_auth:
            token = self._get_platform_token()
            if not token:
                self._warn_delivery_skipped(len(events))
                return
            headers['Authorization'] = f'Bearer {token}'

        session = requests.Session()
        try:
            for batch in self._batches(events):
                response = session.post(
                    self.url,
                    headers=headers,
                    json=batch,
                    verify=self._ssl_verify,
                    timeout=self._timeout,
                    auth=self._basic_auth())
                if response.status_code != 200:
                    response.raise_for_status()
        finally:
            session.close()

        if self._token_warning_emitted:
            self._token_warning_emitted = False
            logger.info("Logstash delivery resumed after platform token became available.")

    @staticmethod
    def _get_platform_token() -> Optional[str]:
        """Return a valid Aion platform token or None when unavailable."""
        from aion.api.http import aion_jwt_manager
        return aion_jwt_manager.get_token_sync()

    def _warn_delivery_skipped(self, events_count: int) -> None:
        """Warn once that events are dropped because no platform token is available."""
        if self._token_warning_emitted:
            return
        self._token_warning_emitted = True
        logger.warning(
            "Logstash requires an Aion platform token but none is available; "
            "dropping %s log event(s). Delivery will resume once "
            "authentication succeeds.",
            events_count,
        )

    def _basic_auth(self) -> Optional[HTTPBasicAuth]:
        """Return HTTP basic auth credentials when configured, otherwise None."""
        if self._username is None or self._password is None:
            return None
        return HTTPBasicAuth(self._username, self._password)

    def _batches(self, events: list):
        """Generate dynamic sized batches based on the max content length.

        Reimplements the private ``HttpTransport.__batches`` since it is
        name-mangled and not accessible to subclasses.

        Args:
            events: A list of already formatted (JSON string) events.
        """
        current_batch = []
        event_iter = iter(events)
        while True:
            try:
                current_event = next(event_iter)
            except StopIteration:
                current_event = None
                if not current_batch:
                    return
                yield current_batch
            if current_event is None:
                return
            if len(current_event) > self._max_content_length:
                logger.warning(
                    "The event size <%s> is greater than the max content "
                    "length <%s>. Skipping event.",
                    len(current_event), self._max_content_length)
                continue
            obj = json.loads(current_event)
            content_length = len(json.dumps(current_batch + [obj]).encode('utf8'))
            if content_length > self._max_content_length:
                batch = current_batch
                current_batch = [obj]
                yield batch
            else:
                current_batch += [obj]
