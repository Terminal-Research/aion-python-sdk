"""Service for registering a new deployment version with the Aion platform."""

import asyncio
import random

from aion.api.gql import AionGqlContextClient
from aion.api.gql.generated.graphql_client import (
    GraphQLClientHttpError,
    GraphQLClientGraphQLMultiError
)
from aion.api.http import aion_jwt_manager
from aion.api.http.jwt_manager import describe_exception
from aion.core.settings import api_settings
from aion.server.settings import app_settings

from aion.server.services import BaseExecuteService

REGISTRATION_BASE_DELAY_SECONDS = 4.0
REGISTRATION_MAX_DELAY_SECONDS = 120.0
REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE = 3
MAX_BACKOFF_EXPONENT = 16
"""Backoff for version registration: 4s doubling to a ceiling of two minutes.

Transient failures are retried for as long as the deployment runs. A budget was
the worst of both worlds: registering is the platform reading back the endpoint we
serve, so it fails until the tunnel or ingress in front of us starts routing - and
if waiting can fix that, there is no hour at which it stops being able to. Giving
up only made a restart the price of a slow tunnel, which is the whole reason an
agent could sit unregistered until somebody noticed.

Nothing is gained by staying quiet in the meantime, so the warning does not wait
for the retries to run out: every failure is a warning until the
REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE-th, which says the deployment is now
serving locally, and the retrying goes to debug from there. A refusal - a
rejection on the merits rather than a transient code - still ends the attempt at
once, at error.

Warning three is the last one on purpose. What follows it cannot change without
somebody acting on it, so repeating it would only teach the reader that these
warnings are worth skipping - which costs more than the silence does, because the
line that ends the silence is a successful registration and that is good news.
"""

RETRYABLE_GRAPHQL_ERROR_CODES = frozenset({
    "SERVICE_UNAVAILABLE",
    "TIMEOUT",
})
"""GraphQL error codes that answer "not now" rather than "no".

Registration is the platform reading back the runtime manifest this agent is
serving, so it fails whenever the agent's public endpoint is not up yet - a tunnel
that has not finished opening reports SERVICE_UNAVAILABLE, which is exactly what
the backoff exists to wait out. Treated as a refusal, that one attempt was the
whole retry budget and the deployment stayed unregistered until it was restarted
by hand.

Deliberately narrow: INTERNAL_SERVER_ERROR is left out because it is the code a
backend emits for anything it did not expect, deterministic bugs included, and
retrying those only delays the warning the operator needs to see.
"""


class AionDeploymentRegisterVersionService(BaseExecuteService):
    """
    Service for registering deployment versions with the Aion platform.

    This service starts version registration with the Aion platform during
    application startup. Runtime metadata is now resolved by the backend, which
    fetches the deployed runtime manifest or remote agent card as needed.

    The service uses the GraphQL client to communicate with the Aion platform and
    handles initialization failures gracefully by logging errors instead of raising
    exceptions.
    """

    async def execute(self, agent_ids: list[str]) -> bool:
        """
        Execute version registration with the Aion platform.

        Registers the configured deployment version via GraphQL. The agent IDs
        argument is retained for compatibility with the serve handler, but the
        SDK no longer serializes or sends a manifest for this mutation.

        Retries transient failures indefinitely, so this returns as soon as the
        platform accepts the version and otherwise runs for as long as the caller
        keeps it alive. Callers therefore run it in the background and cancel it on
        shutdown.

        Args:
            agent_ids: Agent identifiers discovered by the serve handler.

        Returns:
            bool: True once registration succeeded, False if it was refused
        """
        attempt = 0

        while True:
            attempt += 1
            try:
                async with AionGqlContextClient() as client:
                    await client.register_version(version_id=app_settings.version_id)
            except Exception as ex:
                failure = self._describe_failure(ex)

                if not self._is_retryable(ex):
                    self._report_refusal(failure, ex)
                    return False

                delay = self._delay_before_retry(attempt)
                self._log_retry(attempt, delay, failure)
                await asyncio.sleep(delay)
                continue

            self._log_success(attempt)
            return True

    @staticmethod
    def _delay_before_retry(attempt: int) -> float:
        """Wait longer after each failure, up to the ceiling.

        Jittered so that deployments knocked offline together by one platform
        outage do not all come back asking on the same tick.
        """
        delay = min(
            REGISTRATION_BASE_DELAY_SECONDS * 2 ** min(attempt - 1, MAX_BACKOFF_EXPONENT),
            REGISTRATION_MAX_DELAY_SECONDS,
        )
        return delay * (0.5 + random.random() / 2)

    @staticmethod
    def _describe_failure(ex: Exception) -> str:
        """Render one failure as a single line, in the terms its type carries.

        A GraphQL error's ``path`` is dropped: it names the mutation this service
        exists to send, so it says nothing the surrounding line has not.
        """
        if isinstance(ex, GraphQLClientHttpError):
            return (f"HTTP {ex.status_code}: "
                    f"{ex.response.content.decode('utf-8', errors='replace')}")

        if isinstance(ex, GraphQLClientGraphQLMultiError):
            return "; ".join(
                f"{(error.extensions or {}).get('code', 'GraphQL error')}: "
                f"{error.message}"
                for error in ex.errors)

        return describe_exception(ex)

    def _log_success(self, attempt: int) -> None:
        """Announce a successful registration, saying what it ends.

        A run that reached local mode announced that state and then stopped
        mentioning it, so this line is the only thing that revokes it and has to
        say so rather than reading as a bare success.
        """
        if attempt == 1:
            self.logger.info("Version registration completed successfully")
        elif attempt < REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE:
            self.logger.info(
                "Version registration succeeded on attempt %d", attempt)
        else:
            self.logger.info(
                "Version registration succeeded on attempt %d: the deployment is "
                "no longer serving locally and is connecting to the platform",
                attempt)

    def _log_retry(self, attempt: int, delay: float, failure: str) -> None:
        """Report a failed attempt that will be tried again.

        Every failure is a warning: a deployment the platform cannot reach is not
        ordinary, and the first attempts are the ones an operator can still act on
        cheaply. The REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE-th says what the
        failures have added up to - agents that serve but receive nothing - and is
        deliberately the last of them.

        Everything after it drops to debug. It cannot say anything the local-mode
        line has not: the endpoint that is not up yet fails identically every time,
        and repeating that hourly would teach the reader to skip exactly the level
        this service uses to reach them. The state is left standing until the
        success line revokes it.

        That line promises to report again rather than naming the level it will
        report at. Which level is this module's business, and saying it out loud
        both spends a reader's attention on our plumbing and dates the message the
        next time these levels move.
        """
        if attempt < REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE:
            self.logger.warning(
                "Version registration attempt %d failed, retrying in %.0fs: %s",
                attempt, delay, failure)
            return

        if attempt > REGISTRATION_ATTEMPTS_BEFORE_LOCAL_MODE:
            self.logger.debug(
                "Version registration attempt %d failed, retrying in %.0fs: %s",
                attempt, delay, failure)
            return

        self.logger.warning(
            "Version registration has not succeeded after %d attempt(s) "
            "(%s, VERSION_ID: %s): %s. The agents now serve locally: they answer "
            "on their own endpoints but receive no platform traffic. Registration "
            "keeps retrying in the background and reports again once it succeeds.",
            attempt, api_settings.http_url, app_settings.version_id, failure)


    @classmethod
    def _is_retryable(cls, ex: Exception) -> bool:
        """Decide whether waiting could plausibly change the outcome.

        Retrying a refusal only delays the warning that the operator needs to see,
        so the cases where the platform answered and said no are excluded: rejected
        credentials (which the JWT manager has already latched), a GraphQL error
        that is not explicitly transient, and 4xx. What remains - transport
        failures, 5xx, 429, a token that could not be fetched, and the codes in
        RETRYABLE_GRAPHQL_ERROR_CODES - is the transient class this retry exists
        for.
        """
        if aion_jwt_manager.is_auth_failed:
            return False

        if isinstance(ex, GraphQLClientGraphQLMultiError):
            return cls._is_transient_graphql_error(ex)

        if isinstance(ex, GraphQLClientHttpError):
            return ex.status_code == 429 or ex.status_code >= 500

        return True

    @staticmethod
    def _is_transient_graphql_error(ex: GraphQLClientGraphQLMultiError) -> bool:
        """Report whether every error in a GraphQL response is worth retrying.

        Every one, not any one: a response that also carries a real refusal fails
        again on the next attempt no matter how long we wait for the transient half
        of it to clear. An error with no code is a refusal by default - guessing
        that an unlabelled failure is temporary is how a permanent one turns into a
        minute of silence.
        """
        errors = getattr(ex, "errors", None) or []
        if not errors:
            return False

        return all(
            (getattr(error, "extensions", None) or {}).get("code")
            in RETRYABLE_GRAPHQL_ERROR_CODES
            for error in errors)

    def _report_refusal(self, failure: str, ex: Exception) -> None:
        """Report the one outcome retrying cannot change, with the cause spelled out.

        This is the platform rejecting the version on the merits, so it is the end
        of the attempt rather than a pause in it. The line carries the cause rather
        than pointing above at it, since the caller states the consequence
        immediately after.

        Logged above the transient failures it sits among: those say the deployment
        is waiting, this says it has stopped waiting and will not register without
        someone intervening, and at the same level the two are indistinguishable in
        exactly the view an operator would use to tell them apart.
        """
        self.logger.error(
            "Version registration was refused (%s, VERSION_ID: %s): %s",
            api_settings.http_url,
            app_settings.version_id,
            failure,
            # Only a failure this service could not describe still needs its
            # traceback; for the rest the description above is the whole story.
            exc_info=not isinstance(
                ex, (GraphQLClientHttpError, GraphQLClientGraphQLMultiError)))
