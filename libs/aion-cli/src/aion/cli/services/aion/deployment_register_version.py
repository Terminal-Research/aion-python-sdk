from aion.api.gql import AionGqlContextClient
from aion.api.gql.generated.graphql_client import (
    GraphQLClientHttpError,
    GraphQLClientGraphQLMultiError
)
from aion.proxy.utils import generate_a2a_manifest
from aion.shared.settings import app_settings

from aion.shared.services import BaseExecuteService


class AionDeploymentRegisterVersionService(BaseExecuteService):
    """
    Service for registering deployment version manifest with the Aion platform.

    This service generates and registers a proxy manifest containing agent endpoint
    information with the Aion platform during application startup, enabling proper
    service discovery and version management.

    The service uses the GraphQL client to communicate with the Aion platform and
    handles initialization failures gracefully by logging errors instead of raising
    exceptions.
    """

    async def execute(self, agent_ids: list[str]) -> bool:
        """
        Execute the manifest registration to the Aion platform.

        Generates a proxy manifest from the provided agent IDs and registers it
        with the Aion platform via GraphQL client. If the client initialization
        or registration fails, logs an error.

        Args:
            agent_ids: List of agent identifiers to include in the manifest

        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            manifest = generate_a2a_manifest(agent_ids)
            version_id = app_settings.version_id

            async with AionGqlContextClient() as client:
                await client.register_version(manifest=manifest, version_id=version_id)
                self.logger.info("Manifest registration completed successfully")
            return True
        except GraphQLClientHttpError as ex:
            self.logger.error(
                "Manifest registration failed. Status code: %s. Error: \"%s\"",
                ex.status_code,
                ex.response.content.decode("utf-8"),)
            return False
        except GraphQLClientGraphQLMultiError as ex:
            self.logger.error(
                "Manifest registration failed with GraphQL errors. Errors: %s",
                [{"message": e.message, "path": e.path, "extensions": e.extensions} for e in ex.errors]
            )
            return False
        except Exception as ex:
            self.logger.error("Manifest registration failed: %s", ex)
            return False
