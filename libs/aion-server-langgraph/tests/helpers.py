from unittest.mock import Mock

from a2a.types import Message, Part, Role, Task
from aion.shared.agent.adapters import ExecutionSnapshot, ExecutionStatus, InterruptInfo
from aion.server_langgraph.execution.stream_executor import StreamResult
from langchain_core.messages import AIMessage, AIMessageChunk


def make_ai_message(content="Hello", id=None):
    """Create a real AIMessage with text content."""
    return AIMessage(content=content, id=id)


def make_mock_chunk(chunk_position=None, id=None):
    """Create a mock AIMessageChunk with controllable chunk_position."""
    chunk = Mock(spec=AIMessageChunk)
    chunk.chunk_position = chunk_position
    chunk.id = id
    return chunk


def make_execution_config(context_id=None):
    """Create a mock ExecutionConfig."""
    config = Mock()
    config.context_id = context_id
    return config


def make_execution_snapshot(state=None, interrupted=False, metadata=None):
    """Create an ExecutionSnapshot with controlled status."""
    status = ExecutionStatus.INTERRUPTED if interrupted else ExecutionStatus.COMPLETE
    return ExecutionSnapshot(
        state=state or {},
        status=status,
        metadata=metadata or {},
    )


def make_stream_result(delta_text=""):
    """Create a StreamResult with given delta_text."""
    return StreamResult(delta_text=delta_text)


def make_a2a_part(text=None, url=None, raw=None, media_type=None):
    """Create an A2A Part."""
    kwargs = {}
    if text is not None:
        kwargs["text"] = text
    if url is not None:
        kwargs["url"] = url
    if raw is not None:
        kwargs["raw"] = raw
    if media_type is not None:
        kwargs["media_type"] = media_type
    return Part(**kwargs)


def make_interrupt_info(id="interrupt-1", value="Input needed", prompt=None):
    """Create an InterruptInfo object."""
    return InterruptInfo(id=id, value=value, prompt=prompt)


def make_a2a_message(task_id="task-1", context_id="ctx-1", message_id="msg-1", parts=None):
    """Create an A2A Message."""
    return Message(
        task_id=task_id,
        context_id=context_id,
        message_id=message_id,
        role=Role.ROLE_AGENT,
        parts=parts or [],
    )


def make_mock_request_context(task_id="task-1", context_id="ctx-1", message=None):
    """Create a mock A2A RequestContext."""
    ctx = Mock()
    ctx.task_id = task_id
    ctx.context_id = context_id
    ctx.message = message
    return ctx
