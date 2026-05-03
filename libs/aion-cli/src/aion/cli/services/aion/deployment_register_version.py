from aion.api.gql import AionGqlContextClient
from aion.api.gql.generated.graphql_client import (
    GraphQLClientHttpError,
    GraphQLClientGraphQLMultiError
)
from aion.shared.settings import app_settings

from aion.shared.services import BaseExecuteService


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
        try:
            version_id = app_settings.version_id

            async with AionGqlContextClient() as client:
                await client.register_version(version_id=version_id)
                self.logger.info("Version registration completed successfully")
            return True
        except GraphQLClientHttpError as ex:
            self.logger.error(
                "Version registration failed. Status code: %s. Error: \"%s\"",
                ex.status_code,
                ex.response.content.decode("utf-8"),)
            return False
        except GraphQLClientGraphQLMultiError as ex:
            self.logger.error(
                "Version registration failed with GraphQL errors. Errors: %s",
                [{"message": e.message, "path": e.path, "extensions": e.extensions} for e in ex.errors]
            )
            return False
        except Exception as ex:
            self.logger.error("Version registration failed: %s", ex)
            return False
