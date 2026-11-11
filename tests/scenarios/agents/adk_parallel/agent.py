"""A ``ParallelAgent`` over two agents, each with a model that streams.

ADK runs the sub-agents side by side and forwards their events as they come,
so the chunks of the two answers reach the adapter interleaved. Whatever the
user sends, each branch's model streams ``branch_answer`` of its own name.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

from tests.scenarios.agents.native_script import BRANCH_DELAY_SECONDS, BRANCHES, branch_answer, tokens

__all__ = ["create_agent"]

AGENT_NAME = "parallel_agent"


def _response(text: str, *, partial: bool) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), partial=partial)


class BranchLlm(BaseLlm):
    """A model that streams its branch's answer a word at a time, slowly enough to interleave."""

    model: str = "branch"
    branch: str

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        answer = branch_answer(self.branch)
        if stream:
            for piece in tokens(answer):
                await asyncio.sleep(BRANCH_DELAY_SECONDS)
                yield _response(piece, partial=True)
        yield _response(answer, partial=False)


def create_agent() -> ParallelAgent:
    """Build the agent: one sub-agent per branch, run at once."""
    return ParallelAgent(
        name=AGENT_NAME,
        sub_agents=[LlmAgent(name=branch, model=BranchLlm(branch=branch)) for branch in BRANCHES],
    )
