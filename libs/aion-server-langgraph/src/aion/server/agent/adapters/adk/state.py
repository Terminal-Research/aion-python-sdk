from typing import Any, Optional

from aion.shared.agent.adapters import AgentState, InterruptInfo
from aion.shared.logging import get_logger

logger = get_logger()


class ADKStateAdapter:

    @staticmethod
    def from_adk_session(session: Any) -> AgentState:
        # Extract state values from session
        values = {}
        if hasattr(session, "state") and session.state:
            values = session.state

        # Extract next steps if available
        next_steps = []
        if hasattr(session, "next_steps"):
            next_steps = session.next_steps or []

        # Check if session is interrupted/waiting for input
        is_interrupted = False
        interrupt_info = None

        if hasattr(session, "status"):
            # ADK sessions may have a status field indicating state
            is_interrupted = session.status in ["waiting", "interrupted", "input_required"]

        if is_interrupted:
            interrupt_info = ADKStateAdapter._extract_interrupt_info_from_session(session)

        return AgentState(
            values=values,
            next_steps=next_steps,
            is_interrupted=is_interrupted,
            interrupt=interrupt_info,
        )

    @staticmethod
    def _extract_interrupt_info_from_session(session: Any) -> Optional[InterruptInfo]:
        reason = None
        prompt = None
        metadata = {}

        # Try to extract interrupt reason
        if hasattr(session, "interrupt_reason"):
            reason = session.interrupt_reason

        # Try to extract prompt for user
        if hasattr(session, "interrupt_prompt"):
            prompt = session.interrupt_prompt

        # Extract any additional metadata
        if hasattr(session, "metadata"):
            metadata = session.metadata or {}

        if reason or prompt:
            return InterruptInfo(
                reason=reason or "Agent requires input",
                prompt=prompt,
                metadata=metadata,
            )

        return None

    @staticmethod
    def create_resume_input(
        user_input: Optional[dict[str, Any]],
        current_state: AgentState
    ) -> Any:
        if user_input:
            return user_input

        return None

    @staticmethod
    def extract_interrupt_info(state: AgentState) -> Optional[InterruptInfo]:
        if state.is_interrupted:
            return state.interrupt
        return None
