# aion-plugin-langgraph

LangGraph plugin for AION framework.

## Custom Events

The plugin provides helper functions to emit custom events during graph execution. All events are emitted via LangGraph's `StreamWriter` and converted to ExecutionEvent types.

### Available Functions

#### `emit_file(writer, *, url=None, base64=None, mime_type, name=None, append=False, is_last_chunk=True)`

Emit file artifacts during graph execution. Converts to a2a Artifact with FilePart.

**Parameters:**
- `writer`: LangGraph StreamWriter from node signature
- `url`: File URL (for remote files) - mutually exclusive with `base64`
- `base64`: File content as base64 string - mutually exclusive with `url`
- `mime_type`: MIME type (e.g., "application/pdf", "image/png")
- `name`: Artifact name (defaults to "file")
- `append`: Set to `True` to append to previously sent artifact (for streaming)
- `is_last_chunk`: Set to `False` if more chunks are coming

**Use cases:**
- Sending generated PDFs, images, or documents
- Streaming large files in chunks
- Referencing external file resources

#### `emit_data(writer, data, name=None, append=False, is_last_chunk=True)`

Emit structured data artifacts. Data must be JSON-serializable.

**Parameters:**
- `writer`: LangGraph StreamWriter from node signature
- `data`: Dict or any JSON-serializable value
- `name`: Artifact name (defaults to "data")
- `append`: Set to `True` to append to previously sent artifact (for streaming)
- `is_last_chunk`: Set to `False` if more chunks are coming

**Use cases:**
- Sending analysis results, metrics, or structured outputs
- Streaming data in chunks
- Sending JSON-formatted responses

#### `emit_message(writer, message)`

Emit programmatic messages during graph execution. Use for messages not returned in state. Only `AIMessage` is saved to conversation history; `AIMessageChunk` is used for streaming but not persisted.

**Parameters:**
- `writer`: LangGraph StreamWriter from node signature
- `message`: LangChain `AIMessage` or `AIMessageChunk`

**Use cases:**
- Sending intermediate progress messages (use `AIMessage`)
- Emitting streaming text chunks (use `AIMessageChunk`)
- Programmatic message generation (not from LLM output)

#### `emit_task_metadata(writer, metadata)`

Update task metadata during execution. Metadata is merged (not replaced) on server side. Protected keys with `aion:` prefix are ignored and cannot be modified.

**Parameters:**
- `writer`: LangGraph StreamWriter from node signature
- `metadata`: Dictionary with metadata fields to update

**Use cases:**
- Tracking execution progress
- Storing custom metrics or timestamps
- Adding execution context information

### Usage

```python
from langgraph.types import StreamWriter
from aion.langgraph.stream import emit_file, emit_data, emit_message, emit_task_metadata

def my_node(state: dict, writer: StreamWriter):
    # Emit file artifact
    emit_file(writer, url="https://example.com/report.pdf", mime_type="application/pdf")

    # Emit data artifact
    emit_data(writer, {"status": "success", "results": [...]}, name="analysis")

    # Emit message
    emit_message(writer, AIMessage(content="Processing complete"))

    # Update task metadata
    emit_task_metadata(writer, {"progress": 100})

    return state
```
