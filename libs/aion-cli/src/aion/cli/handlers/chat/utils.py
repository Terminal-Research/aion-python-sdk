import secrets
import uuid
from typing import Dict, Any, Optional

from aion.shared.types.a2a.extensions.distribution import (
    DISTRIBUTION_EXTENSION_URI_V1,
    AgentIdentityRecord,
    BehaviorRecord,
    DistributionExtensionV1,
    DistributionRecord,
    EnvironmentRecord,
)
from aion.shared.types.a2a.extensions.traceability import (
    TRACEABILITY_EXTENSION_URI_V1,
    TraceStateEntry,
    TraceabilityExtensionV1,
)


class A2ARequestHelper:
    def __init__(self, sender_id: Optional[str] = None, node_id: Optional[str] = None):
        self.sender_id = sender_id or "aion:user:2244994945"
        self.node_id = node_id or "cli-node-local"

    def generate_task_metadata(
            self,
            agent_name: str = "Test Agent",
            agent_username: str = "testagent",
            behavior_key: str = "testGraph",
            environment_name: str = "Development",
            system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        org_id = str(uuid.uuid4())
        trace_id = secrets.token_hex(16)
        span_id = secrets.token_hex(8)

        distribution = DistributionExtensionV1(
            sender_id=self.sender_id,
            distribution=DistributionRecord(
                id=str(uuid.uuid4()),
                endpoint_type="Aion",
                url="https://example.com/agent-card",
                identities=[
                    AgentIdentityRecord(
                        kind="agent",
                        id=str(uuid.uuid4()),
                        network_type="Aion",
                        represented_user_id=str(uuid.uuid4()),
                        organization_id=org_id,
                        display_name=agent_name,
                        user_name=agent_username,
                        avatar_image_url="https://example.com/avatar.png",
                        agent_type="Deployed",
                        url="https://example.com/agent",
                    )
                ],
            ),
            behavior=BehaviorRecord(
                id=str(uuid.uuid4()),
                behavior_key=behavior_key,
                version_id=str(uuid.uuid4()),
            ),
            environment=EnvironmentRecord(
                id=str(uuid.uuid4()),
                name=environment_name,
                deployment_id=str(uuid.uuid4()),
                configuration_variables={
                    "API_TIMEOUT": "30",
                    "MAX_RETRIES": "3",
                    "LOG_LEVEL": "INFO",
                },
                system_prompt=system_prompt or f"You are {agent_name}, a helpful assistant.",
            ),
        )

        traceability = TraceabilityExtensionV1(
            traceparent=f"00-{trace_id}-{span_id}-01",
            tracestate=[TraceStateEntry(key="aion", value=span_id)],
            baggage={
                "aion.sender.id": self.node_id,
                "channel": "cli",
                "tenant": "local",
            },
        )

        distribution_val = distribution.model_dump(by_alias=True, exclude_none=True)
        traceability_val = traceability.model_dump(by_alias=True, exclude_none=True)
        del distribution_val["version"]
        del traceability_val["version"]

        return {
            DISTRIBUTION_EXTENSION_URI_V1: distribution_val,
            TRACEABILITY_EXTENSION_URI_V1: traceability_val,
        }
