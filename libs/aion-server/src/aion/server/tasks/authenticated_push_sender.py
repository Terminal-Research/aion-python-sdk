"""Push-notification sender that authenticates webhook calls against external servers."""

import asyncio
import httpx
import logging
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.push_notification_sender import PushNotificationEvent
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

DEFAULT_AUTH_SCHEME = 'Bearer'
RESPONSE_SUMMARY_LIMIT = 200


def _describe(event: PushNotificationEvent) -> str:
    """Names the event a log line is about.

    A task pushes many times over a run — a status update per transition, an
    update per artifact chunk, and the terminal Task — to a single callback URL.
    Without a discriminator every delivery logs an identical line, and the
    questions this log is opened with cannot be answered from it: whether the
    terminal Task landed or only an intermediate ``WORKING``, and which artifact
    the receiver refused.

    The distinction also ranks failures. A rejection of an intermediate status
    is cosmetic, since the next update supersedes it; a rejection of the
    terminal Task leaves the client without an outcome.

    Args:
        event: The event being delivered.

    Returns:
        A short, single-line description of the event.
    """
    if isinstance(event, Task):
        return f'Task state={_state_name(event.status.state)}'
    if isinstance(event, TaskStatusUpdateEvent):
        return f'TaskStatusUpdateEvent state={_state_name(event.status.state)}'
    if isinstance(event, TaskArtifactUpdateEvent):
        return (
            f'TaskArtifactUpdateEvent '
            f'artifact={event.artifact.artifact_id or "<unidentified>"} '
            f'last_chunk={event.last_chunk}'
        )
    return type(event).__name__


def _state_name(state: int) -> str:
    """Renders a task state as its declared name.

    Args:
        state: The enum value carried by the event.

    Returns:
        The enum name, or the raw value when this build does not know it —
        proto3 keeps unrecognised enum numbers, so a peer on a newer schema
        must not turn a log line into a traceback.
    """
    try:
        return TaskState.Name(state)
    except ValueError:
        return str(state)


def _timing(response: httpx.Response) -> str:
    """Reports how long the receiver took to answer.

    Delivery latency is the observable behind ``PUSH_NOTIFICATION_TIMEOUT_SECONDS``:
    a receiver creeping towards the budget is only visible here, before it
    starts failing as ``ReadTimeout``.

    Args:
        response: The answered response.

    Returns:
        A leading-comma fragment to append to a log line, empty when httpx has
        no timing for this response — it records one per transport round-trip,
        so a response built by hand (a test double, a cached answer) has none.
    """
    try:
        return f', {response.elapsed.total_seconds() * 1000:.0f} ms'
    except RuntimeError:
        return ''


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
    the base class builds its header dict inline with no extension point. The
    fan-out around it is reimplemented for the same reason — see
    ``send_notification``. ``test_sdk_override_parity`` pins both overrides to
    the base signatures so an SDK upgrade that changes them fails at test time
    rather than at delivery time.
    """

    async def send_notification(
            self, task_id: str, event: PushNotificationEvent
    ) -> None:
        """Delivers one event to every webhook registered for the task.

        Mirrors the base class, which posts to all stored configurations
        concurrently and swallows individual failures, and differs only in what
        it reports afterwards. The base logs a bare "some notifications failed"
        line on any failure; since every failing dispatch has already logged the
        URL, the status and the receiver's message, that line repeats a subset
        of what is on the line above it — and a task carries a single webhook in
        the common case, so it is pure duplication there.

        The summary is therefore kept only where it says something the
        per-webhook lines cannot: when the event fanned out to more than one
        receiver, and then with the counts, so the log states how much of the
        fan-out got through without the reader tallying it.

        Args:
            task_id: Identifier of the task the event belongs to.
            event: The event to deliver.
        """
        push_configs = await self._config_store.get_info_for_dispatch(task_id)
        if not push_configs:
            return

        results = await asyncio.gather(
            *[
                self._dispatch_notification(event, push_info, task_id)
                for push_info in push_configs
            ]
        )

        failed = results.count(False)
        if failed and len(results) > 1:
            logger.warning(
                'Push-notification fan-out: %s of %s deliveries failed — %s',
                failed,
                len(results),
                _describe(event),
            )

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
            task_id: Identifier of the task the event belongs to. Declared by
                the base class and accepted for that contract, but not written
                into the messages below: ``ServerAionContextFilter`` puts the
                id on every record as a structured ``task_id`` field before it
                reaches a handler, so repeating it in the text would print the
                same value twice on the same line.

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
                'Push-notification sent to URL: %s — %s (HTTP %s%s)',
                url,
                _describe(event),
                response.status_code,
                _timing(response),
            )
        except httpx.HTTPStatusError as error:
            # The webhook answered and refused. The status and the receiver's own
            # message are the whole diagnosis — 401/403 points at the credentials
            # on the config, 5xx at the receiver — so a stack trace of our call
            # frames only buries it. The body is what distinguishes a gateway
            # error from the application's own refusal, and it is truncated
            # because a receiver may answer with a full HTML page.
            logger.warning(
                'Push-notification rejected by URL: %s — %s — HTTP %s: %s',
                url,
                _describe(event),
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
                'Push-notification timed out to URL: %s — %s (%s). '
                'Raise PUSH_NOTIFICATION_TIMEOUT_SECONDS if the receiver is '
                'expected to be this slow.',
                url,
                _describe(event),
                type(error).__name__,
            )
            return False
        except Exception:
            logger.exception(
                'Error sending push-notification to URL: %s — %s.',
                url,
                _describe(event),
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
