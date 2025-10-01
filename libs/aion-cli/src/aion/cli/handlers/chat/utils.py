import uuid
from typing import Dict, Any, Optional


class A2ARequestHelper:
    def __init__(self, sender_id: Optional[str] = None):
        """
        Initialize the metadata generator

        Args:
            sender_id: ID of the message sender
        """
        self.sender_id = sender_id or str(uuid.uuid4())

    def generate_task_metadata(
            self,
            network: str = "Aion",
            agent_name: str = "Test Agent",
            agent_at_name: str = "@TestAgent",
            graph_id: str = "testGraph",
            environment_name: str = "Development",
            system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate metadata dictionary with mock/test data

        Args:
            network: Source network name (default: "Aion")
            agent_name: Display name of the agent (default: "Test Agent")
            agent_at_name: Addressable name in Aion network (default: "@TestAgent")
            graph_id: Langgraph graph name (default: "testGraph")
            environment_name: Environment name (default: "Development")
            system_prompt: Custom system prompt (optional)

        Returns:
            Dictionary with complete A2A metadata structure
        """
        return {
            "aion:senderId": self.sender_id,
            "aion:network": network,
            "aion:traceId": str(uuid.uuid4()),
            "aion:distribution": {
                "id": str(uuid.uuid4()),
                "identity": {
                    "id": str(uuid.uuid4()),
                    "userId": str(uuid.uuid4()),
                    "name": agent_name,
                    "atName": agent_at_name,
                    "biography": f"This is a test agent named {agent_name}",
                    "avatarImageUrl": "https://example.com/avatar.png",
                    "backgroundImageUrl": "https://example.com/background.png"
                }
            },
            "aion:behavior": {
                "id": str(uuid.uuid4()),
                "graphId": graph_id,
                "versionId": str(uuid.uuid4())
            },
            "aion:environment": {
                "id": str(uuid.uuid4()),
                "name": environment_name,
                "configurationVariables": {
                    "API_TIMEOUT": "30",
                    "MAX_RETRIES": "3",
                    "LOG_LEVEL": "INFO"
                },
                "useLongTermMemory": False,
                "systemPrompt": system_prompt or f"You are {agent_name}, a helpful assistant."
            }
        }
