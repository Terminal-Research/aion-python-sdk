import logging
import os
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
)
from langgraph.checkpoint.memory import MemorySaver, PersistentDict

from langgraph_api.serde import Serializer

logger = logging.getLogger(__name__)

_EXCLUDED_KEYS = {"checkpoint_ns", "checkpoint_id", "run_id", "thread_id"}


class InMemorySaver(MemorySaver):
    def __init__(
        self,
        *,
        serde: SerializerProtocol | None = None,
    ) -> None:
        self.filename = os.path.join(".langgraph_api", ".langgraph_checkpoint.")
        i = 0

        def factory(*args):
            nonlocal i
            i += 1

            thisfname = self.filename + str(i) + ".pckl"
            d = PersistentDict(*args, filename=thisfname)
            if not os.path.exists(".langgraph_api"):
                os.mkdir(".langgraph_api")
            try:
                d.load()
            except FileNotFoundError:
                pass
            except ModuleNotFoundError:
                logger.error(
                    "Unable to load cached data - your code has changed in a way that's incompatible with the cache."
                    "\nThis usually happens when you've:"
                    "\n  - Renamed or moved classes"
                    "\n  - Changed class structures"
                    "\n  - Pulled updates that modified class definitions in a way that's incompatible with the cache"
                    "\n\nRemoving invalid cache data stored at path: .langgraph_api"
                )
                os.remove(self.filename)
            except Exception as e:
                logger.error("Failed to load cached data: %s", str(e))
                os.remove(self.filename)
            return d

        super().__init__(
            serde=serde if serde is not None else Serializer(),
            factory=factory,
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig:
        # TODO: Should this be done in OSS as well?
        metadata = {
            **{
                k: v
                for k, v in config["configurable"].items()
                if not k.startswith("__") and k not in _EXCLUDED_KEYS
            },
            **config.get("metadata", {}),
            **metadata,
        }
        if not isinstance(checkpoint["id"], uuid.UUID):
            # Avoid type inconsistencies
            checkpoint = checkpoint.copy()
            checkpoint["id"] = str(checkpoint["id"])
        return super().put(config, checkpoint, metadata, new_versions)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        if isinstance(config["configurable"].get("checkpoint_id"), uuid.UUID):
            # Avoid type inconsistencies....
            config = config.copy()

            config["configurable"] = {
                **config["configurable"],
                "checkpoint_id": str(config["configurable"]["checkpoint_id"]),
            }
        return super().get_tuple(config)

    def clear(self):
        self.storage.clear()
        self.writes.clear()
        for suffix in ["1", "2"]:
            file_path = f"{self.filename}{suffix}.pckl"
            if os.path.exists(file_path):
                os.remove(file_path)


MEMORY = InMemorySaver()


def Checkpointer(*args, **kwargs):
    return MEMORY


__all__ = ["Checkpointer"]
