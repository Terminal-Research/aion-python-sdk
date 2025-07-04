# Generated by ariadne-codegen
# Source: gql/schema.graphql

from enum import Enum


class AgentBehaviorDeploymentType(str, Enum):
    Aion = "Aion"
    LangGraph = "LangGraph"


class AgentIdentityType(str, Enum):
    Deployed = "Deployed"
    Personal = "Personal"


class DeploymentType(str, Enum):
    GitHub = "GitHub"
    Local = "Local"


class Privacy(str, Enum):
    Private = "Private"
    Public = "Public"


class SubjectType(str, Enum):
    User = "User"
    Version = "Version"


class UserNetworkType(str, Enum):
    Aion = "Aion"
    Telegram = "Telegram"


class VersionStatus(str, Enum):
    Building = "Building"
    Cancelled = "Cancelled"
    Error = "Error"
    Offline = "Offline"
    Online = "Online"
    Provisioning = "Provisioning"
    Queued = "Queued"
