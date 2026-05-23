from unittest.mock import Mock

from aion.core.runtime import AionRuntimeContext
from aion.core.types.a2a.extensions.distribution import (
    PrincipalIdentity,
    Behavior,
    DistributionExtensionV1,
    Distribution,
    Environment,
    ServiceIdentity,
)


def make_mock_inbox(message=None, task=None):
    inbox = Mock()
    inbox.message = message
    inbox.task = task
    return inbox


def make_mock_distribution_extension(endpoint_type="A2A", include_principal=True, include_service=False):
    identities = []
    if include_principal:
        identities.append(
            PrincipalIdentity(
                kind="principal",
                id="agent-1",
                network_type="aion",
                organization_id="org-1",
            )
        )
    if include_service:
        identities.append(
            ServiceIdentity(
                kind="service",
                id="svc-1",
                network_type=endpoint_type,
                organization_id="org-1",
            )
        )

    return DistributionExtensionV1(
        distribution=Distribution(
            id="dist-1",
            endpoint_type=endpoint_type,
            url="test://distribution",
            identities=identities,
        ),
        behavior=Behavior(id="beh-1", behavior_key="main", version_id="v-1"),
        environment=Environment(
            id="env-1",
            name="prod",
            deployment_id="dep-1",
            configuration_variables={},
        ),
    )


def make_mock_event(kind=None, payload=None):
    event = Mock()
    event.kind = kind
    event.payload = payload
    return event


def make_mock_context(event=None, inbox=None, distribution_extension_payload=None):
    return AionRuntimeContext(
        event=event,
        inbox=inbox if inbox is not None else make_mock_inbox(),
        distributionExtensionPayload=distribution_extension_payload,
    )


def make_mock_runtime(context=None):
    runtime = Mock()
    runtime.context = context
    return runtime
