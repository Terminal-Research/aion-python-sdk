"""Push-notification sender that authenticates webhook calls against external servers."""

import httpx
import logging
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.push_notification_sender import PushNotificationEvent
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

DEFAULT_AUTH_SCHEME = 'Bearer'
RESPONSE_SUMMARY_LIMIT = 200


def _summarize(response: httpx.Response) -> str:
    """Condenses a rejection response body into one loggable line.

    Args:
        response: The refusal returned by the webhook.

    Returns:
        The body collapsed onto a single line and truncated, or a placeholder
        when the body is empty or could not be decoded.
    """
    try:
        body = ' '.join(response.text.split())
    except Exception:
        return '<undecodable body>'
    if not body:
        return '<empty body>'
    if len(body) > RESPONSE_SUMMARY_LIMIT:
        return f'{body[:RESPONSE_SUMMARY_LIMIT]}…'
    return body


class AuthenticatedPushNotificationSender(BasePushNotificationSender):
    """Applies ``PushNotificationConfig.authentication`` to the outbound webhook call.

    The A2A schema lets a client declare how the receiving server authenticates
    its callbacks — ``TaskPushNotificationConfig.authentication`` carries a
    ``scheme``/``credentials`` pair, and the platform populates it when it hands
    us a callback URL. The store round-trips that field intact, but the SDK's
    ``BasePushNotificationSender`` only ever emits the legacy
    ``X-A2A-Notification-Token`` header, so every notification reaches an
    authenticated endpoint anonymously and is rejected with 401/403.

    This sender restores the missing half: the declared credentials go out as a
    standard ``Authorization`` header. The notification token keeps its own
    header, so a config that sets both is delivered with both, and a config that
    sets neither behaves exactly as it did before.

    The whole dispatch body is reimplemented rather than delegated to, because
    the base class builds its header dict inline with no extension point.
    ``test_sdk_override_parity`` pins the override to the base signature so an
    SDK upgrade that changes it fails at test time rather than at delivery time.
    """

    async def _dispatch_notification(
            self,
            event: PushNotificationEvent,
            push_info: TaskPushNotificationConfig,
            task_id: str,
    ) -> bool:
        """Posts a single notification to one configured webhook.

        Args:
            event: The event to deliver, serialized as a stream response.
            push_info: The stored configuration for this webhook, including the
                target URL and any declared authentication.
            task_id: Identifier of the task the event belongs to, for logging.

        Returns:
            True when the webhook accepted the delivery, False when the request
            failed. Failures are logged and swallowed, matching the base class:
            one unreachable webhook must not abort the fan-out to the others.
        """
        url = push_info.url
        try:
            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(event)),
                headers=self._build_headers(push_info) or None,
            )
            response.raise_for_status()
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s', task_id, url
            )
        except httpx.HTTPStatusError as error:
            # The webhook answered and refused. The status and the receiver's own
            # message are the whole diagnosis — 401/403 points at the credentials
            # on the config, 5xx at the receiver — so a stack trace of our call
            # frames only buries it. The body is what distinguishes a gateway
            # error from the application's own refusal, and it is truncated
            # because a receiver may answer with a full HTML page.
            logger.warning(
                'Push-notification rejected for task_id=%s by URL: %s — HTTP %s: %s',
                task_id,
                url,
                error.response.status_code,
                _summarize(error.response),
            )
            return False
        except httpx.TimeoutException as error:
            # The request went out but the receiver did not answer in time.
            # Routine for a slow webhook, and tunable via
            # PUSH_NOTIFICATION_TIMEOUT_SECONDS, so it is reported as a
            # condition rather than as a crash.
            logger.warning(
                'Push-notification timed out for task_id=%s to URL: %s (%s). '
                'Raise PUSH_NOTIFICATION_TIMEOUT_SECONDS if the receiver is '
                'expected to be this slow.',
                task_id,
                url,
                type(error).__name__,
            )
            return False
        except Exception:
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                task_id,
                url,
            )
            return False
        return True

    @staticmethod
    def _build_headers(push_info: TaskPushNotificationConfig) -> dict[str, str]:
        """Derives the outbound headers from a stored push configuration.

        Args:
            push_info: The stored configuration for the target webhook.

        Returns:
            Mapping of header names to values. Empty when the configuration
            declares neither a notification token nor credentials.

            X-A2A-Notification-Token  — the opaque token from ``push_info.token``,
                                        the SDK's own webhook-validation channel.
            Authorization             — ``"<scheme> <credentials>"`` from
                                        ``push_info.authentication``, defaulting to
                                        the Bearer scheme when the client declared
                                        credentials without naming a scheme.

        Credential values are never logged: an exception raised while sending
        carries only the task id and the URL.
        """
        headers: dict[str, str] = {}

        if push_info.token:
            headers['X-A2A-Notification-Token'] = push_info.token

        authentication = push_info.authentication
        if authentication.credentials:
            scheme = authentication.scheme or DEFAULT_AUTH_SCHEME
            headers['Authorization'] = f'{scheme} {authentication.credentials}'

        return headers


__all__ = ['AuthenticatedPushNotificationSender']
