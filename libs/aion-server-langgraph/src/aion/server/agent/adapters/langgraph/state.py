from typing import Any, Optional

from aion.shared.agent.adapters import (
    AgentState,
    InterruptInfo,
    StateAdapter,
)
from aion.shared.logging import get_logger
from langgraph.types import Command, Interrupt, StateSnapshot

logger = get_logger()


class LangGraphStateAdapter(StateAdapter):

    def get_state_from_snapshot(self, snapshot: StateSnapshot) -> AgentState:
        logger.debug("Converting LangGraph StateSnapshot to AgentState")
        next_steps = list(snapshot.next) if snapshot.next else []
        is_interrupted = self._has_interrupt(snapshot)
        messages = []

        if hasattr(snapshot, "values") and isinstance(snapshot.values, dict):
            messages = snapshot.values.get("messages", [])

        metadata = {
            "langgraph_snapshot": True,
            "created_at": snapshot.created_at if hasattr(snapshot, "created_at") else None,
            "parent_config": snapshot.parent_config if hasattr(snapshot, "parent_config") else None,
        }
        if is_interrupted:
            interrupt_data = self._extract_interrupt_data(snapshot)
            if interrupt_data:
                metadata["interrupt_data"] = interrupt_data

        agent_state = AgentState(
            values=snapshot.values if hasattr(snapshot, "values") else {},
            next_steps=next_steps,
            is_interrupted=is_interrupted,
            metadata=metadata,
            config=snapshot.config if hasattr(snapshot, "config") else {},
            messages=messages,
        )

        logger.debug(
            f"AgentState created: interrupted={is_interrupted}, next_steps={next_steps}"
        )

        return agent_state

    def extract_interrupt_info(self, state: AgentState) -> Optional[InterruptInfo]:
        if not state.is_interrupted:
            return None

        logger.debug("Extracting interrupt info from state")
        interrupt_data = state.metadata.get("interrupt_data")

        if not interrupt_data:
            return InterruptInfo(reason="unknown_interrupt")
        if isinstance(interrupt_data, list) and len(interrupt_data) > 0:
            first_interrupt = interrupt_data[0]
            if isinstance(first_interrupt, dict):
                return InterruptInfo(
                    reason="human_input_required",
                    prompt=first_interrupt.get("value", "Input required"),
                    metadata=first_interrupt,
                )

        return InterruptInfo(
            reason="interrupt_occurred",
            metadata={"raw_data": interrupt_data},
        )

    def create_resume_input(
            self,
            user_input: Any,
            state: AgentState,
    ) -> dict[str, Any]:
        logger.debug(f"Creating resume input with user_input: {user_input}")
        return Command(resume=user_input)

    @staticmethod
    def _has_interrupt(snapshot: StateSnapshot) -> bool:
        """Check if snapshot contains any interrupts.

        Args:
            snapshot: LangGraph state snapshot

        Returns:
            True if any task has interrupts
        """
        if not hasattr(snapshot, "tasks"):
            return False

        tasks = snapshot.tasks
        if not tasks:
            return False

        for task in tasks:
            if hasattr(task, "interrupts"):
                interrupts = task.interrupts
                if isinstance(interrupts, list) and len(interrupts) > 0:
                    return True

        return False

    @staticmethod
    def _extract_interrupt_data(snapshot: StateSnapshot) -> Optional[list]:
        """Extract interrupt data from all tasks in snapshot.

        Args:
            snapshot: LangGraph state snapshot

        Returns:
            List of interrupt data or None
        """
        if not hasattr(snapshot, "tasks"):
            return None

        all_interrupts = []

        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                for interrupt in task.interrupts:
                    if isinstance(interrupt, Interrupt):
                        all_interrupts.append(
                            {
                                "value": interrupt.value,
                                "when": interrupt.when if hasattr(interrupt, "when") else None,
                            }
                        )
                    else:
                        all_interrupts.append(interrupt)

        return all_interrupts if all_interrupts else None
