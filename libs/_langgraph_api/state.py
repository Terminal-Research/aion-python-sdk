from langchain_core.runnables.config import RunnableConfig
from langgraph.types import StateSnapshot

from langgraph_api.schema import Checkpoint, ThreadState


def runnable_config_to_checkpoint(
    config: RunnableConfig | None,
) -> Checkpoint | None:
    if (
        not config
        or not config["configurable"]
        or "thread_id" not in config["configurable"]
        or not config["configurable"]["thread_id"]
        or "checkpoint_id" not in config["configurable"]
        or not config["configurable"]["checkpoint_id"]
    ):
        return None

    configurable = config["configurable"]
    checkpoint: Checkpoint = {
        "checkpoint_id": configurable["checkpoint_id"],
        "thread_id": configurable["thread_id"],
    }

    if "checkpoint_ns" in configurable:
        checkpoint["checkpoint_ns"] = configurable["checkpoint_ns"] or ""

    if "checkpoint_map" in configurable:
        checkpoint["checkpoint_map"] = configurable["checkpoint_map"]

    return checkpoint


def state_snapshot_to_thread_state(state: StateSnapshot) -> ThreadState:
    return {
        "values": state.values,
        "next": state.next,
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "path": t.path,
                "error": t.error,
                "interrupts": t.interrupts,
                "checkpoint": t.state["configurable"]
                if t.state is not None and not isinstance(t.state, StateSnapshot)
                else None,
                "state": state_snapshot_to_thread_state(t.state)
                if isinstance(t.state, StateSnapshot)
                else None,
                "result": getattr(t, "result", None),
            }
            for t in state.tasks
        ],
        "metadata": state.metadata,
        "created_at": state.created_at,
        "checkpoint": runnable_config_to_checkpoint(state.config),
        "parent_checkpoint": runnable_config_to_checkpoint(state.parent_config),
        # below are deprecated
        "checkpoint_id": state.config["configurable"].get("checkpoint_id")
        if state.config
        else None,
        "parent_checkpoint_id": state.parent_config["configurable"]["checkpoint_id"]
        if state.parent_config
        else None,
    }
