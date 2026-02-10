# A2A ↔ LangGraph Mapping

This document describes how Aion Server adapts A2A requests to LangGraph and maps LangGraph outputs/events back into A2A Messages/Tasks and streaming events.

---

## 1. Inbound Messages

### 1.1 Graph Invocation

Both `message/send` and `message/stream` use the same event generation flow: `AgentExecutor.execute()` always produces an event stream via `graph.astream()`.

The difference is in how `DefaultRequestHandler.ResultAggregator` consumes this stream:

- **`message/send` (blocking=true)** — collects all events and returns the final `Task`.
- **`message/send` (blocking=false)** — returns after the first event, continues processing in background (`status="working"`).
- **`message/stream`** — yields events as they arrive and streams them to the client via SSE.

> **Note:** Older A2A examples (<0.3) used separate `invoke()` / `stream()` paths. In A2A 0.3+ execution is unified — only the consumption strategy differs.

### 1.2 Message Ingress

When an inbound A2A `Message` arrives:

1. **Append to `state.messages` (LLM-facing transcript)**
   - If the inbound A2A `Message` contains one or more **text** parts **and** the graph state includes a `messages` property, Aion Server appends a LangChain `HumanMessage` derived from the A2A text.
   - Default policy: concatenate all A2A `TextPart`s (in order) into a single `HumanMessage`.

2. **Store the raw A2A envelope (transport-facing context)**
   - If the graph state includes an `a2a_inbox` property, Aion Server sets it to an object with:
     - `task`: the current `Task`
     - `message`: the inbound A2A `Message` (full object, including non-text parts)
     - `metadata`: `SendMessageRequest`-level metadata (e.g. distribution/network, trace info)

3. **Idempotency / dedupe**
   - If the inbound A2A `messageId` has already been ingested for the current `contextId`, Aion Server must **not** append a duplicate `HumanMessage`.

---

## 2. Outbound Messages

### 2.1 MessageSendRequest → `graph.astream()`

Valid responses to an A2A `MessageSendRequest` RPC are a **`Message`** or **`Task`**.

Aion Server constructs the response using the following precedence:

#### (1) `a2a_outbox` (authoritative)

If the returned dict contains `a2a_outbox`, it must contain either a **Task** or a **Message**. Server-owned fields are enforced:
- `taskId` and `contextId` are set to the current values managed by Aion Server (developer values ignored).
- Canonical routing/identity metadata (e.g. `aion:network`, sender IDs) is server-controlled.

Behavior:
- If `a2a_outbox` is a **Message** → append to current Task history.
- If `a2a_outbox` is a **Task** → treat as a **patch** to the server's Task:
  - server merges/extends history and artifacts
  - graph-provided metadata merges shallowly (server-controlled keys take precedence)

Also: keep `state.messages` in sync by appending an `AIMessage` (and/or `ToolMessage`) derived from the outbound A2A payload. Linkage: `AIMessage.id = a2a.messageId`.

#### (2) Fallback: derive A2A Message from `state.messages`

If no `a2a_outbox` exists and the returned dict contains a `messages` list — use the **last `AIMessage`** to construct an outbound A2A `Message` with `role='agent'` and a single `TextPart`.

> If a developer needs to return a comprehensive A2A `Message` (e.g., `DataPart`s, rich metadata, or extension context), they should set `a2a_outbox` rather than relying on inferred fallbacks.

---

## 3. Streaming

### 3.1 MessageStreamRequest → `graph.astream()`

Valid responses to an A2A `MessageStreamRequest` are:
- `TaskStatusUpdateEvent`
- `TaskArtifactUpdateEvent`
- `Message`
- `Task`

Aion Server requests LangGraph stream updates using `stream_mode=["values", "messages", "custom", "updates"]`.

Each outbound SSE frame is emitted as a **StreamResponse** wrapper containing exactly one of:
`{ statusUpdate } | { artifactUpdate } | { message } | { task }`

### 3.2 Event Type: `values`

The **last** `values` payload in the stream represents the final output/state snapshot. Aion Server uses it to update Task state and determine the final terminal response (if one hasn't already been sent).

Output mapping follows the same precedence as Section 2.1, with one addition:

#### (3) If neither `a2a_outbox` nor `messages` exist

Aion Server constructs an A2A `Message` using accumulated streamed deltas collected in the `"aion:streamDelta"` Artifact via `messages` mode (see 3.3).

### 3.3 Event Type: `messages`

`messages` stream mode yields **LLM output chunks** as `(message_chunk, metadata)`. These events are **not** diffs to `state.messages`.

> **Important:** multiple LLM invocations in a graph can produce `messages` events.

Bridging to A2A — chunks are appended into a transitory streaming artifact:
- `artifact.name = "Stream Delta"`
- `artifact.id = "aion:stream-delta"`
- `append=true` for each chunk
- `lastChunk=true` once on completion

A `TaskArtifactUpdateEvent` is emitted for each chunk. This artifact is **transitory** and is not persisted to the Task's durable state by default.

### 3.4 Event Type: `custom`

The Aion SDK provides helper functions (via LangGraph's `StreamWriter`) to emit custom events during graph execution. Aion Server listens for these `custom` payloads and forwards them as A2A events, enforcing canonical `taskId` and `contextId`.

**Precedence rule:** explicit A2A streaming events emitted via `custom` are authoritative.

#### Available `custom` emit functions

All functions are imported from `aion.langgraph.stream`:

```python
from langgraph.types import StreamWriter
from aion.langgraph.stream import emit_file, emit_data, emit_message, emit_task_metadata
```

---

##### `emit_file(writer, *, url=None, base64=None, mime_type, name=None, append=False, is_last_chunk=True)`

Emits a file artifact. Converts to an A2A `Artifact` with `FilePart`.

| Parameter | Description |
|---|---|
| `writer` | LangGraph `StreamWriter` from node signature |
| `url` | File URL for remote files — mutually exclusive with `base64` |
| `base64` | File content as base64 string — mutually exclusive with `url` |
| `mime_type` | MIME type (e.g. `"application/pdf"`, `"image/png"`) |
| `name` | Artifact name (defaults to `"file"`) |
| `append` | Set to `True` to append to a previously sent artifact |
| `is_last_chunk` | Set to `False` if more chunks are coming |

Use cases: sending generated PDFs/images/documents, streaming large files in chunks, referencing external resources.

---

##### `emit_data(writer, data, name=None, append=False, is_last_chunk=True)`

Emits a structured data artifact. `data` must be JSON-serializable.

| Parameter | Description |
|---|---|
| `writer` | LangGraph `StreamWriter` from node signature |
| `data` | Dict or any JSON-serializable value |
| `name` | Artifact name (defaults to `"data"`) |
| `append` | Set to `True` to append to a previously sent artifact |
| `is_last_chunk` | Set to `False` if more chunks are coming |

Use cases: sending analysis results, metrics, structured outputs, JSON-formatted responses.

---

##### `emit_message(writer, message)`

Emits a programmatic message during graph execution. Use for messages not returned in state.

| Parameter | Description |
|---|---|
| `writer` | LangGraph `StreamWriter` from node signature |
| `message` | LangChain `AIMessage` or `AIMessageChunk` |

> Only `AIMessage` is saved to conversation history. `AIMessageChunk` is used for streaming but not persisted.

Use cases: intermediate progress messages (`AIMessage`), streaming text chunks (`AIMessageChunk`), programmatic message generation.

---

##### `emit_task_metadata(writer, metadata)`

Updates task metadata during execution. Metadata is **merged** (not replaced) on the server side. Protected keys with `aion:` prefix are ignored and cannot be modified.

| Parameter | Description |
|---|---|
| `writer` | LangGraph `StreamWriter` from node signature |
| `metadata` | Dictionary with metadata fields to update |

Use cases: tracking execution progress, storing custom metrics or timestamps, adding execution context.

---

##### Usage example

```python
from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage
from aion.langgraph.stream import emit_file, emit_data, emit_message, emit_task_metadata

def my_node(state: dict, writer: StreamWriter):
    # Emit file artifact
    emit_file(writer, url="https://example.com/report.pdf", mime_type="application/pdf")

    # Emit data artifact
    emit_data(writer, {"status": "success", "results": [...]}, name="analysis")

    # Emit message (not from LLM output)
    emit_message(writer, AIMessage(content="Processing complete"))

    # Update task metadata
    emit_task_metadata(writer, {"progress": 100})

    return state
```

### 3.5 Event Type: `updates` (optional)

Primarily used for debugging/observability. Not required for A2A protocol mapping.

---

## 4. Summary of Responsibilities

### LangGraph Graph Author

- Keep `state.messages` as LangChain message types.
- Optionally set `a2a_outbox` for full-fidelity A2A responses.
- For streaming, optionally emit A2A-native events via `custom` using the SDK helper functions.

### Aion Server Adapter

- Own canonical IDs and routing metadata.
- Ensure idempotency on ingress.
- Map LangGraph output/state into A2A `Message`/`Task`.
- Stream A2A events as `StreamResponse` wrappers.