import json
import mimetypes
from collections.abc import AsyncIterator
from typing import Any, Optional, TYPE_CHECKING

from aion.shared.agent.exceptions import ExecutionError, StateRetrievalError
from aion.shared.agent.adapters import (
    CompleteEvent,
    ErrorEvent,
    ExecutionConfig,
    ExecutionEvent,
    ExecutionSnapshot,
    ExecutorAdapter,
    InterruptEvent,
)
from aion.shared.config.models import AgentConfig
from aion.shared.logging import get_logger
from langchain_core.messages import HumanMessage
from langchain_core.messages.content import (
    create_text_block,
    create_file_block,
    TextContentBlock,
    FileContentBlock,
)

from ..events import LangGraphEventConverter
from ..state import LangGraphStateAdapter

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = get_logger()


class LangGraphExecutor(ExecutorAdapter):
    def __init__(self, compiled_graph: Any, config: AgentConfig):
        self.compiled_graph = compiled_graph
        self.config = config
        self.state_adapter = LangGraphStateAdapter()
        self.event_converter = LangGraphEventConverter()

    async def _execute_and_convert_stream(
            self,
            inputs: Any,
            config: dict[str, Any],
            stream_mode: list[str]
    ) -> AsyncIterator[ExecutionEvent]:
        """
        Executes the LangGraph stream and converts internal events to unified ExecutionEvents.

        Args:
            inputs: The data to pass to astream (could be state updates or a Command object).
            config: LangGraph-specific configuration (thread_id, etc.).
            stream_mode: List of LangGraph streaming modes to subscribe to.

        Yields:
            Converted unified ExecutionEvent objects.
        """
        async for event_type, event_data in self.compiled_graph.astream(
                inputs,
                config,
                stream_mode=stream_mode,
        ):
            # LangGraph returns a tuple (message, metadata) specifically for the "messages" stream mode
            if event_type == "messages":
                event_data, metadata = event_data
            else:
                metadata = None

            # Transform the vendor-specific event into our system's unified event format
            unified_event = self.event_converter.convert(
                event_type, event_data, metadata
            )

            if unified_event:
                yield unified_event

    async def _handle_final_state(self, final_state: ExecutionSnapshot) -> AsyncIterator[ExecutionEvent]:
        """
        Analyzes the final state of the graph to determine if it finished
        normally or was interrupted for user input.

        Args:
            final_state: The snapshot of the graph state after execution.

        Yields:
            Either an InterruptEvent or a CompleteEvent.
        """
        # Check if the graph is paused at a breakpoint or waiting for input
        if final_state.requires_input():
            # Extract all interrupts (LangGraph 0.6.0+ supports multiple)
            all_interrupts = self.state_adapter.extract_all_interrupts(final_state)
            yield InterruptEvent(interrupts=all_interrupts)
        else:
            yield CompleteEvent()

    async def stream(
            self,
            context: "RequestContext",
            config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        try:
            langgraph_config = self._to_langgraph_config(config)
            langgraph_inputs = self._transform_context(context)

            async for event in self._execute_and_convert_stream(
                    langgraph_inputs,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"]
            ):
                yield event

            final_state = await self.get_state(config)
            async for final_event in self._handle_final_state(final_state):
                yield final_event

        except Exception as e:
            logger.error(f"LangGraph stream failed: {e}")
            yield ErrorEvent(
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ExecutionError(f"Failed to stream agent: {e}") from e

    async def resume(
            self,
            context: "RequestContext",
            config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        if not config or not config.context_id:
            raise ValueError("context_id is required to resume execution")

        try:
            logger.info(f"Resuming execution for context: {config.context_id}")
            state = await self.get_state(config)

            if not state.requires_input():
                logger.warning(
                    f"Attempted to resume non-interrupted execution: {config.context_id}"
                )
                # If not interrupted, continue with new inputs or raise error
                user_input = context.get_user_input()
                if not user_input:
                    raise ValueError(
                        f"Execution {config.context_id} is not interrupted, "
                        "but no new inputs provided"
                    )
                async for event in self.stream(context, config):
                    yield event
                return

            # Transform context to LangGraph format for resume
            transformed_inputs = self._transform_context(context)

            # Create Command object for resume (LangGraph-specific)
            resume_command = self.state_adapter.create_resume_input(transformed_inputs, state)

            logger.debug(f"Resuming with command: {resume_command}")

            # Stream using the Command object directly
            langgraph_config = self._to_langgraph_config(config)

            async for event in self._execute_and_convert_stream(
                    resume_command,
                    langgraph_config,
                    stream_mode=["values", "messages", "custom", "updates"]
            ):
                yield event

            final_state = await self.get_state(config)
            async for final_event in self._handle_final_state(final_state):
                yield final_event

        except Exception as e:
            logger.error(f"Failed to resume execution: {e}")
            raise ExecutionError(f"Failed to resume execution: {e}") from e

    async def get_state(self, config: ExecutionConfig) -> ExecutionSnapshot:
        if not config or not config.context_id:
            raise ValueError("context_id is required to get state")

        try:
            langgraph_config = self._to_langgraph_config(config)
            snapshot = await self.compiled_graph.aget_state(langgraph_config)
            execution_snapshot = self.state_adapter.get_state_from_snapshot(snapshot)
            return execution_snapshot

        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            raise StateRetrievalError(f"Failed to retrieve state: {e}") from e

    @staticmethod
    def _detect_mime_type(file_info: Any) -> str:
        """Detect MIME type from file_info object.

        Tries to determine MIME type in the following order:
        1. Use explicit mime_type attribute if present
        2. Guess from filename extension if name attribute is present
        3. Fall back to application/octet-stream

        Args:
            file_info: File information object (FileWithBytes or FileWithUri)

        Returns:
            MIME type string
        """
        # First try to use explicit mime_type if present and not None
        mime_type = getattr(file_info, 'mime_type', None)
        if mime_type:
            return mime_type

        # Try to guess from filename
        filename = getattr(file_info, 'name', None)
        if filename:
            guessed_type, _ = mimetypes.guess_type(filename)
            if guessed_type:
                return guessed_type

        # Default fallback
        return 'application/octet-stream'

    @staticmethod
    def _to_langgraph_config(config: Optional[ExecutionConfig]) -> dict[str, Any]:
        """Convert ExecutionConfig to LangGraph configuration format.

        Args:
            config: Execution configuration with context_id

        Returns:
            LangGraph config dict with thread_id (mapped from context_id)
        """
        if not config:
            return {}

        if not config.context_id:
            return {}

        return {"configurable": {"thread_id": config.context_id}}

    @staticmethod
    def _transform_context(context: "RequestContext") -> dict[str, Any]:
        """Transform A2A RequestContext to LangGraph format.

        Converts RequestContext to LangGraph's expected message format,
        including text content and file attachments using LangChain message types.

        Args:
            context: A2A request context

        Returns:
            LangGraph-compatible input dict with HumanMessage including text and files
        """
        if not context.message:
            return {"messages": []}

        # Build content blocks for LangChain HumanMessage
        content_blocks: list[TextContentBlock | FileContentBlock] = []

        for part in context.message.parts:
            part_obj = part.root

            # Handle text parts
            if part_obj.kind == 'text':
                content_blocks.append(create_text_block(text=part_obj.text))

            # Handle file parts
            elif part_obj.kind == 'file':
                file_info = part_obj.file
                # Detect MIME type from file_info (checks mime_type, name, or defaults)
                mime_type = LangGraphExecutor._detect_mime_type(file_info)

                # Handle base64-encoded bytes
                if hasattr(file_info, 'bytes'):
                    file_base64 = file_info.bytes
                    content_blocks.append(
                        create_file_block(
                            base64=file_base64,
                            mime_type=mime_type,
                        )
                    )

                # Handle URI-based files
                elif hasattr(file_info, 'uri'):
                    content_blocks.append(
                        create_file_block(
                            url=file_info.uri,
                            mime_type=mime_type,
                        )
                    )

            # Handle data parts - convert to text
            elif part_obj.kind == 'data':
                data_text = json.dumps(part_obj.data, indent=2)
                content_blocks.append(create_text_block(text=data_text))

        # If no content was extracted, return empty messages
        if not content_blocks:
            return {"messages": []}

        # Create HumanMessage with content blocks (LangChain standard)
        human_message = HumanMessage(content=content_blocks)
        return {"messages": [human_message]}
