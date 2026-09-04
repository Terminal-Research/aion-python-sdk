"""Aion Google ADK authoring toolkit."""

from aion.core.utils.optional_deps import is_own_module, missing_extra_error

# This package ships in every wheel, but google-adk and litellm only arrive
# with the extra. Without them the import fails deep inside a third-party
# module, on a name that means nothing to whoever wrote the agent.
try:
    from .mcp import (
        aion_adk_mcp_toolset,
        aion_adk_mcp_toolsets_sync,
        default_adk_runtime_context,
    )
    from .models import aion_lite_llm
    from .invocation.emitters import emit_artifact, emit_card, emit_reaction, emit_message
except ModuleNotFoundError as exc:
    if is_own_module(exc.name):
        raise
    raise missing_extra_error("Google ADK authoring", "adk-authoring", exc) from exc

__all__ = [
    "aion_adk_mcp_toolset",
    "aion_adk_mcp_toolsets_sync",
    "default_adk_runtime_context",
    "aion_lite_llm",
    "emit_artifact",
    "emit_card",
    "emit_reaction",
    "emit_message",
]
