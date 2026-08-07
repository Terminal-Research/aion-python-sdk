"""Service for registering a new deployment version with the Aion platform."""

import asyncio

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

REGISTRATION_ATTEMPTS = 5
REGISTRATION_BASE_DELAY_SECONDS = 4.0
"""Retry budget for version registration: 4s, 8s, 16s, 32s - about a minute in all.

Registration is a single shot fired during startup, so before this a connect
timeout shorter than the request itself left the agent running but invisible to
the platform until someone restarted it by hand. Bounded rather than endless: if a
minute of retrying does not help, the cause is not a network hiccup and the warning
below is the useful outcome.
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

        Args:
            agent_ids: Agent identifiers discovered by the serve handler.

        Returns:
            bool: True if registration was successful, False otherwise
        """
        for attempt in range(1, REGISTRATION_ATTEMPTS + 1):
            try:
                version_id = app_settings.version_id

                async with AionGqlContextClient() as client:
                    await client.register_version(version_id=version_id)
                    if attempt > 1:
                        self.logger.info(
                            "Version registration succeeded on attempt %d", attempt)
                    else:
                        self.logger.info("Version registration completed successfully")
                return True
            except Exception as ex:
                self._log_failure(ex, attempt)

                if attempt == REGISTRATION_ATTEMPTS or not self._is_retryable(ex):
                    self._warn_unregistered(attempt)
                    return False

                delay = REGISTRATION_BASE_DELAY_SECONDS * 2 ** (attempt - 1)
                self.logger.info(
                    "Retrying version registration in %.0fs (attempt %d of %d)",
                    delay, attempt + 1, REGISTRATION_ATTEMPTS)
                await asyncio.sleep(delay)

        return False

    def _log_failure(self, ex: Exception, attempt: int) -> None:
        """Report one failed attempt in the terms its exception type carries."""
        prefix = f"Version registration attempt {attempt}/{REGISTRATION_ATTEMPTS} failed"

        if isinstance(ex, GraphQLClientHttpError):
            self.logger.error(
                "%s. Status code: %s. Error: \"%s\"",
                prefix, ex.status_code, ex.response.content.decode("utf-8"))
        elif isinstance(ex, GraphQLClientGraphQLMultiError):
            self.logger.error(
                "%s with GraphQL errors. Errors: %s",
                prefix,
                [{"message": e.message, "path": e.path, "extensions": e.extensions}
                 for e in ex.errors])
        else:
            self.logger.error("%s: %s", prefix, describe_exception(ex), exc_info=True)

    @staticmethod
    def _is_retryable(ex: Exception) -> bool:
        """Decide whether waiting could plausibly change the outcome.

        Retrying a refusal only delays the warning that the operator needs to see,
        so the cases where the platform answered and said no are excluded: rejected
        credentials (which the JWT manager has already latched), GraphQL errors, and
        4xx. What remains - transport failures, 5xx, 429, a token that could not be
        fetched - is the transient class this retry exists for.
        """
        if aion_jwt_manager.is_auth_failed:
            return False

        if isinstance(ex, GraphQLClientGraphQLMultiError):
            return False

        if isinstance(ex, GraphQLClientHttpError):
            return ex.status_code == 429 or ex.status_code >= 500

        return True

    def _warn_unregistered(self, attempts: int) -> None:
        """State the consequence of a failed registration, not just the failure.

        Registration runs as a detached task and its return value is discarded, so
        the single error line above is followed straight away by the welcome banner
        and a working local server. That reads as success. Spell out that the agent
        is up but invisible to the platform, or the operator will not notice.
        """
        self.logger.warning(
            "Agent is running locally but is NOT registered with the Aion platform "
            "after %d attempt(s) (%s, VERSION_ID: %s). It will not receive platform "
            "traffic until registration succeeds - restart once the cause above is "
            "resolved.",
            attempts,
            api_settings.http_url,
            app_settings.version_id)
