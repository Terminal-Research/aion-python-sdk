"""Push-notification sender: a2a-sdk's delivery, from a queue per webhook, with retries."""

import asyncio
import contextvars
import datetime
import email.utils
import httpx
import logging
import math
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.push_notification_sender import PushNotificationEvent, PushNotificationSender
from a2a.types import Task, TaskStatusUpdateEvent
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES
from aion.server.a2a.utils import NO_TEXT, describe_event, extract_event_preview
from aion.server.files.links import StoredFileLinks
from aion.server.files.storage import UploadContext
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from google.protobuf.json_format import MessageToDict
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_AUTH_SCHEME = 'Bearer'
RESPONSE_SUMMARY_LIMIT = 200

RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
"""The pauses before the retries of one delivery, in order."""

MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1
"""The attempts one delivery makes at most: the first and one per pause."""

MAX_RETRY_AFTER_SECONDS = RETRY_DELAYS_SECONDS[-1]
"""The longest pause a receiver's ``Retry-After`` can ask for."""

QUEUE_LIMIT = 1000
"""The events that wait for one webhook of one task at most."""

RETRYABLE_STATUS_CODES = frozenset({408, 429})
"""The refusals other than 5xx that a later attempt can get past."""

# The event vocabulary is shared with the streaming path — see describe_event.


def _report_content(event: PushNotificationEvent) -> None:
    """Report what a queued event carries, when it carries anything new.

    The push channel answers a non-streaming turn, where nothing else logs the
    events at all: the outbound projection only runs on a stream. So without
    this line an agent's own words cannot be read back from a ``message/send``
    run, and the delivery lines say only that something was accepted.

    DEBUG and no higher, for the reason the streaming side keeps the same rule:
    an INFO record travels to logstash, and the words are already kept in the
    task store. Reported once per event rather than once per webhook — the
    content does not differ between receivers.

    The terminal Task is skipped. It carries the reply that went out as a status
    update moments earlier, so previewing it prints the same words twice. Unlike
    the streaming path, no message reaches the client through the Task alone:
    the interrupt prompt that the outbound projection withholds there is pushed
    here as the status update it is.

    Args:
        event: The event that was just queued.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    if isinstance(event, Task):
        return
    preview = extract_event_preview(event)
    if preview != NO_TEXT:
        logger.debug('Push-notification content: %r', preview)


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


def _retry_after(response: httpx.Response) -> Optional[float]:
    """The pause the receiver asked for in ``Retry-After``, at most ``MAX_RETRY_AFTER_SECONDS``.

    Args:
        response: The refusal returned by the webhook.

    Returns:
        The seconds to wait, from a number of seconds or an HTTP date; None
        when the header is absent or unreadable.
    """
    value = response.headers.get('Retry-After', '').strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.UTC)
        seconds = (when - datetime.datetime.now(datetime.UTC)).total_seconds()
    if not math.isfinite(seconds) and seconds != math.inf:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _is_retryable(status_code: int) -> bool:
    """Whether a refusal with ``status_code`` is worth another attempt."""
    return status_code in RETRYABLE_STATUS_CODES or 500 <= status_code <= 599


def is_task_outcome(event: PushNotificationEvent) -> bool:
    """Whether ``event`` is a task's outcome: it moves the task out of the active states."""
    return (
        isinstance(event, Task | TaskStatusUpdateEvent)
        and event.status.state in NON_ACTIVE_TASK_STATES
    )


def _copy(event: PushNotificationEvent) -> PushNotificationEvent:
    """A copy of ``event`` that later changes to the original do not reach."""
    copy = type(event)()
    copy.CopyFrom(event)
    return copy


@dataclass(frozen=True)
class _Attempt:
    """What one POST to a webhook came to.

    Attributes:
        acknowledged: The webhook answered with a success status.
        retryable: The failure is one a later attempt can get past.
        failure: The log line that describes the failure.
        hint: Advice appended to the line when the delivery gives up.
        retry_after: The pause the receiver asked for, in seconds.
        reported: The failure is already logged, with its traceback.
        success: The status and the timing of an acknowledged attempt.
    """

    acknowledged: bool
    retryable: bool = False
    failure: str = ''
    hint: str = ''
    retry_after: Optional[float] = None
    reported: bool = False
    success: str = ''


@dataclass(eq=False)
class _Delivery:
    """One event waiting for one webhook.

    Attributes:
        event: The event, copied when it was queued.
        link_scope: The scope the event's file links are asked for in.
        context: The context the event was queued in: its log fields, and
            the execution scope the queuing request ran in. Every attempt
            runs in it, as the delivery did when it was made at once.
        outcome: The event is the task's outcome.
    """

    event: PushNotificationEvent
    link_scope: Optional[UploadContext]
    context: Optional[contextvars.Context]
    outcome: bool


class _WebhookQueue:
    """The events that wait for one webhook of one task, in the order they were queued.

    One worker delivers them, one at a time. ``failing`` is set once the
    event being delivered has failed an attempt; while it is set, the
    intermediate events waiting in front of a queued outcome of the task are
    dropped - the outcome is the whole Task, and carries what they carried.
    While the webhook answers, nothing is dropped.
    """

    def __init__(self, task_id: str, push_info: TaskPushNotificationConfig) -> None:
        self.task_id = task_id
        self.push_info = push_info
        self.waiting: deque[_Delivery] = deque()
        self.sending: Optional[_Delivery] = None
        self.failing = False
        self.overflow = 0
        self.worker: Optional[asyncio.Task[None]] = None

    @property
    def url(self) -> str:
        return self.push_info.url

    def put(self, delivery: _Delivery) -> None:
        """Queue ``delivery`` behind the others; a full queue first drops its oldest intermediate event."""
        if len(self.waiting) >= QUEUE_LIMIT:
            self._drop_oldest()
        self.waiting.append(delivery)
        if delivery.outcome and self.failing:
            self.drop_superseded()

    def take(self) -> Optional[_Delivery]:
        """The next event to deliver, or None when nothing waits."""
        self.sending = self.waiting.popleft() if self.waiting else None
        self.failing = False
        if self.overflow and len(self.waiting) < QUEUE_LIMIT:
            logger.info(
                'Push-notification queue to URL: %s is below its limit again; '
                '%d event(s) were dropped',
                self.url,
                self.overflow,
            )
            self.overflow = 0
        return self.sending

    def drop_superseded(self) -> None:
        """Drop the intermediate events waiting in front of the last queued outcome."""
        last = max((i for i, waiting in enumerate(self.waiting) if waiting.outcome), default=None)
        if last is None:
            return
        kept = deque(
            waiting for i, waiting in enumerate(self.waiting) if waiting.outcome or i > last
        )
        dropped = len(self.waiting) - len(kept)
        if dropped:
            self.waiting = kept
            logger.info(
                'Push-notification to URL: %s is failing; %d waiting event(s) dropped, '
                'the task outcome queued after them carries them',
                self.url,
                dropped,
            )

    def unsent(self) -> int:
        """The events not delivered yet: those waiting and the one being delivered."""
        return len(self.waiting) + (1 if self.sending is not None else 0)

    def _drop_oldest(self) -> None:
        for i, waiting in enumerate(self.waiting):
            if not waiting.outcome:
                del self.waiting[i]
                break
        else:
            return
        if not self.overflow:
            logger.warning(
                'Push-notification queue to URL: %s holds %d events; the oldest '
                'intermediate events are dropped until it drains',
                self.url,
                QUEUE_LIMIT,
            )
        self.overflow += 1


class AionPushNotificationSender(BasePushNotificationSender):
    """a2a-sdk's push delivery, from a queue per webhook, retried, and reported.

    What is sent is the base class's contract: the same headers - the
    notification token as ``X-A2A-Notification-Token``, a config's
    ``authentication`` as ``Authorization`` - the same push URL check, and
    the same best effort: a config store that cannot be read or a URL check
    that raises is logged and never reaches the task, and a URL check that
    raises rejects the URL. One header rule goes further than the base:
    credentials declared without a scheme are sent as ``Bearer``, where the
    base sends no ``Authorization`` at all.

    How it is sent differs. ``send_notification`` puts the event in a queue
    for each webhook of the task and returns; a worker per queue delivers its
    events one at a time, in order, so nothing that queues an event - the
    task's stream, a ``message/send`` answer, a cancel - waits for a
    receiver. A delivery the receiver does not acknowledge is tried again
    when another attempt can get past the failure: a timeout, a connection
    that failed, HTTP 408, 429 or 5xx. The pauses are
    ``RETRY_DELAYS_SECONDS``; a ``Retry-After`` the receiver sends replaces
    the next one, up to ``MAX_RETRY_AFTER_SECONDS``. A timed-out request may
    have reached the receiver, so a retry can deliver an event twice. Each
    webhook is retried on its own; one that keeps failing holds back only
    its own later events. While one fails, a queued outcome replaces the
    intermediate events waiting in front of it (``_WebhookQueue``).

    The log reports every delivery: an accepted one with how long the
    receiver took, a failed attempt that is tried again at INFO, and a
    delivery that gives up - its attempts spent, or a refusal no attempt can
    get past - at WARNING, with the receiver's status and message rather
    than a traceback.

    An event is delivered with working download links: given ``file_links``,
    every expired link of a stored file is replaced before each attempt
    (``aion.server.files.links``), asked for on behalf of ``link_scope`` - the
    usage attribution of the turn the event belongs to, kept when it was
    queued - or, without one, of the deployment's daemon. The outcome of a
    long task carries files written more than a link's lifetime ago.

    The queues live in the process and end with it: ``drain`` waits for
    them, ``aclose`` gives up on what is left.

    The base class builds its request and logs its outcome inline, with no
    extension point, so ``_dispatch_notification`` and ``send_notification``
    are reimplemented rather than delegated to. ``test_sdk_override_parity``
    pins both to the base signatures so an SDK upgrade that changes them
    fails at test time rather than at delivery time.
    """

    def __init__(
            self,
            *args,
            file_links: StoredFileLinks | None = None,
            sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
            **kwargs,
    ) -> None:
        """Build a2a-sdk's sender.

        Args:
            file_links: Refreshes the links of what it delivers.
            sleep: Waits out the pause before a retry.
        """
        super().__init__(*args, **kwargs)
        self._file_links = file_links
        self._sleep = sleep
        self._queues: dict[tuple[str, str, str], _WebhookQueue] = {}
        self._closed = False

    async def drain(self, timeout: float | None = None) -> bool:
        """Wait until every queued event is delivered or given up on.

        Args:
            timeout: The seconds to wait at most; None waits as long as it takes.

        Returns:
            True when nothing is left queued, False when the time ran out first.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while self._queues:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return False
            workers = [queue.worker for queue in self._queues.values() if queue.worker is not None]
            await asyncio.wait(workers, timeout=remaining)
        return True

    async def aclose(self) -> None:
        """Give up on every event still queued, then close the HTTP client and its connection pool."""
        self._closed = True
        queues = list(self._queues.values())
        unsent = sum(queue.unsent() for queue in queues)
        tasks = {queue.task_id for queue in queues if queue.unsent()}
        workers = [queue.worker for queue in queues if queue.worker is not None]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        if unsent:
            logger.warning(
                '%d push-notification(s) of %d task(s) were not delivered before the sender closed',
                unsent,
                len(tasks),
            )
        await self._client.aclose()

    async def send_notification(
            self,
            task_id: str,
            event: PushNotificationEvent,
            *,
            link_scope: UploadContext | None = None,
    ) -> bool:
        """Queues one event for every webhook registered for the task.

        The webhooks are the ones the task has when the event is queued. A
        config store that cannot be read is logged and nothing is queued:
        delivery is best effort, and its failure must not reach the task.

        Args:
            task_id: Identifier of the task the event belongs to.
            event: The event to deliver. A copy is queued: a later change to
                the event does not reach the webhooks.
            link_scope: The scope new download links are asked for in: the
                usage attribution of the turn the event belongs to. None asks
                on behalf of the deployment's daemon.

        Returns:
            True when the event is queued for every webhook, or the task has
            none; False when nothing could be queued.
        """
        if self._closed:
            logger.warning(
                'The push-notification sender is closed; nothing is sent — %s',
                describe_event(event),
            )
            return False
        try:
            push_configs = await self._config_store.get_info_for_dispatch(task_id)
        except Exception:
            logger.exception(
                'Could not read the push-notification configs; nothing is sent — %s',
                describe_event(event),
            )
            return False
        if not push_configs:
            return True

        queued = _copy(event)
        outcome = is_task_outcome(event)
        for push_info in push_configs:
            self._queue(task_id, push_info).put(
                _Delivery(queued, link_scope, contextvars.copy_context(), outcome)
            )
        _report_content(event)
        return True

    async def _dispatch_notification(
            self,
            event: PushNotificationEvent,
            push_info: TaskPushNotificationConfig,
            task_id: str,
            *,
            link_scope: UploadContext | None = None,
    ) -> bool:
        """Delivers one event to one webhook now, with every attempt it is allowed.

        Args:
            event: The event to deliver, serialized as a stream response.
            push_info: The stored configuration for this webhook, including the
                target URL and any declared authentication.
            task_id: Identifier of the task the event belongs to. Declared by
                the base class and accepted for that contract, but not written
                into the log lines: ``ServerAionContextFilter`` puts the id on
                every record as a structured ``task_id`` field before it
                reaches a handler, so repeating it in the text would print the
                same value twice on the same line.
            link_scope: The scope new download links are asked for in.

        Returns:
            True when the webhook acknowledged the delivery, False when the
            URL check refused it or every attempt failed. Failures are logged
            and swallowed, matching the base class.
        """
        return await self._deliver(
            _Delivery(event, link_scope, None, is_task_outcome(event)), push_info
        )

    def _queue(self, task_id: str, push_info: TaskPushNotificationConfig) -> _WebhookQueue:
        """The queue of ``push_info`` for ``task_id``, with its worker running."""
        key = (task_id, push_info.id, push_info.url)
        queue = self._queues.get(key)
        if queue is None:
            queue = _WebhookQueue(task_id, push_info)
            self._queues[key] = queue
            # The worker runs in a context of its own; each delivery runs in
            # the context its event was queued in.
            queue.worker = asyncio.create_task(
                self._run(key, queue),
                name=f'push-notifications:{task_id}',
                context=contextvars.Context(),
            )
        return queue

    async def _run(self, key: tuple[str, str, str], queue: _WebhookQueue) -> None:
        """Deliver the events of ``queue`` until none is left, then retire it."""
        try:
            while (delivery := queue.take()) is not None:
                try:
                    await asyncio.create_task(
                        self._deliver(delivery, queue.push_info, queue),
                        context=delivery.context,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        'Error delivering a push-notification to URL: %s — %s.',
                        queue.url,
                        describe_event(delivery.event),
                    )
        finally:
            queue.sending = None
            if self._queues.get(key) is queue:
                del self._queues[key]

    async def _deliver(
            self,
            delivery: _Delivery,
            push_info: TaskPushNotificationConfig,
            queue: _WebhookQueue | None = None,
    ) -> bool:
        """Deliver ``delivery`` to ``push_info``'s webhook, trying again while another attempt can help.

        Returns:
            True when the webhook acknowledged the delivery.
        """
        if not await self._url_accepted(push_info.url, delivery.event):
            return False
        attempts = 0
        while True:
            attempts += 1
            event = delivery.event
            if self._file_links is not None:
                event = await self._file_links.refresh(event, context=delivery.link_scope)
            attempt = await self._post(event, push_info)
            if attempt.acknowledged:
                logger.info(
                    'Push-notification sent to URL: %s — %s (%s%s)',
                    push_info.url,
                    describe_event(event),
                    attempt.success,
                    f', attempt {attempts} of {MAX_ATTEMPTS}' if attempts > 1 else '',
                )
                return True
            if not attempt.retryable or attempts == MAX_ATTEMPTS:
                break
            if queue is not None:
                queue.failing = True
                queue.drop_superseded()
            delay = (
                attempt.retry_after
                if attempt.retry_after is not None
                else RETRY_DELAYS_SECONDS[attempts - 1]
            )
            logger.info(
                '%s; attempt %d of %d, the next in %gs',
                attempt.failure,
                attempts,
                MAX_ATTEMPTS,
                delay,
            )
            await self._sleep(delay)
        if not attempt.reported:
            logger.warning(
                '%s%s%s',
                attempt.failure,
                f'; gave up after {attempts} attempts' if attempts > 1 else '',
                attempt.hint,
            )
        return False

    async def _url_accepted(self, url: str, event: PushNotificationEvent) -> bool:
        """Whether the push URL check lets ``url`` be posted to; a check that raises rejects it."""
        if self._push_url_validator is None:
            return True
        try:
            return bool(await self._push_url_validator(url))
        except Exception:
            logger.exception(
                'Push-notification URL check failed for URL: %s; the URL is rejected — %s',
                url,
                describe_event(event),
            )
            return False

    async def _post(
            self, event: PushNotificationEvent, push_info: TaskPushNotificationConfig
    ) -> _Attempt:
        """Make one attempt to deliver ``event``, and say what it came to."""
        url = push_info.url
        what = describe_event(event)
        try:
            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(event)),
                headers=self._build_headers(push_info) or None,
            )
        except httpx.TimeoutException as error:
            # The request went out but the receiver did not answer in time.
            # Routine for a slow webhook, and tunable via
            # PUSH_NOTIFICATION_TIMEOUT_SECONDS, so it is reported as a
            # condition rather than as a crash. The receiver may have taken
            # the event all the same.
            return _Attempt(
                acknowledged=False,
                retryable=True,
                failure=f'Push-notification timed out to URL: {url} — {what} ({type(error).__name__})',
                hint='. Raise PUSH_NOTIFICATION_TIMEOUT_SECONDS if the receiver is '
                     'expected to be this slow.',
            )
        except (httpx.NetworkError, httpx.RemoteProtocolError) as error:
            # The connection failed or broke off: the receiver is down or
            # restarting, which a later attempt can get past. Any other
            # transport error - a URL scheme httpx does not speak, a proxy
            # refusal - is no better on the next attempt.
            return _Attempt(
                acknowledged=False,
                retryable=True,
                failure=f'Push-notification could not reach URL: {url} — {what} ({type(error).__name__})',
            )
        except Exception:
            logger.exception('Error sending push-notification to URL: %s — %s.', url, what)
            return _Attempt(acknowledged=False, reported=True)
        if response.is_success:
            return _Attempt(
                acknowledged=True, success=f'HTTP {response.status_code}{_timing(response)}'
            )
        # The webhook answered and refused. The status and the receiver's own
        # message are the whole diagnosis — 401/403 points at the credentials
        # on the config, 5xx at the receiver — so a stack trace of our call
        # frames only buries it. The body is what distinguishes a gateway
        # error from the application's own refusal, and it is truncated
        # because a receiver may answer with a full HTML page.
        retryable = _is_retryable(response.status_code)
        return _Attempt(
            acknowledged=False,
            retryable=retryable,
            failure=(
                f'Push-notification rejected by URL: {url} — {what} — '
                f'HTTP {response.status_code}: {_summarize(response)}'
            ),
            retry_after=_retry_after(response) if retryable else None,
        )

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
        elif authentication.scheme:
            logger.warning(
                'Push config %s sets an authentication scheme with no '
                'credentials; sending no Authorization header',
                push_info.id,
            )

        return headers


async def send_with_link_scope(
        sender: PushNotificationSender,
        task_id: str,
        event: PushNotificationEvent,
        link_scope: UploadContext | None,
) -> bool:
    """Hand ``event`` to ``sender``, the links of its files asked for in ``link_scope``.

    Only ``AionPushNotificationSender`` issues links again, so only it is
    given the scope; any other sender delivers the event as it is.

    Returns:
        Whether the sender took the event: ``AionPushNotificationSender``
        says whether it queued it; any other sender took it once its
        ``send_notification`` returned.
    """
    if isinstance(sender, AionPushNotificationSender):
        return await sender.send_notification(task_id, event, link_scope=link_scope)
    await sender.send_notification(task_id, event)
    return True


__all__ = [
    'AionPushNotificationSender',
    'MAX_ATTEMPTS',
    'QUEUE_LIMIT',
    'RETRY_DELAYS_SECONDS',
    'is_task_outcome',
    'send_with_link_scope',
]
