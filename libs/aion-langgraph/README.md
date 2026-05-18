# aion-langgraph

LangGraph authoring toolkit for Aion. Provides state helpers, streaming utilities, and event-routing primitives that graph authors import directly. Safe to install without pulling in any server or plugin machinery.

---

## Installation

```bash
pip install aion-langgraph
```

Or, if you are serving the agent with Aion:

```bash
pip install aion-cli[langgraph]
```

---

## Models

Use `aion_chat_model` when you want LangGraph/LangChain model calls to flow
through Aion's OpenAI-compatible model proxy:

```python
from aion.langgraph import aion_chat_model

llm = aion_chat_model("model-id-from-control-plane")
```

The helper calls LangChain's `init_chat_model` with `model_provider="openai"`,
`base_url="<AION_API_HOST>/v1"`, and an Aion JWT provider backed by the
configured `AION_CLIENT_ID` and `AION_CLIENT_SECRET`. Any other keyword
arguments are passed through to LangChain. Look up available model IDs in the
Aion control plane model catalog.

---

## Event Routing — `add_event_handlers`

`add_event_handlers` registers a single dispatcher node in the graph that routes inbound A2A events to the appropriate handler based on event kind.

```python
from langgraph.graph import StateGraph
from aion.langgraph import add_event_handlers

builder = StateGraph(State)
builder.add_node("process", process_node)

add_event_handlers(
    builder,
    on_message=handle_message,
    on_reaction=handle_reaction,
    on_command=handle_command,
)
```

Handlers receive only the parameters they declare — any subset is valid:

```python
async def handle_message(thread: Thread, message: Message, state: State):
    await thread.reply(f"Got your message: {message.text}")

async def handle_reaction(context: AionRuntimeContext, event):
    ...
```

Available injectable parameters: `state`, `runtime`, `context`, `event`, `identity`, `inbox`, `thread`, `message`. Any other declared parameter is forwarded to LangGraph for native injection (e.g. `config`, `store`).

---

## State Helpers — `Thread` and `Message`

`Thread` and `Message` are high-level wrappers over the raw A2A inbox. They are injected automatically when declared in a handler registered via `add_event_handlers`.

### Thread

Bound to the current invocation. Wraps reply/post/typing primitives with LangGraph streaming.

```python
async def handle_message(thread: Thread):
    # Durable reply — saved in task history
    await thread.reply("Processing your request...")

    # Ephemeral typing indicator — stream only, not saved
    await thread.typing("Thinking...")

    # Post to an explicit target context
    await thread.post("Update from agent", target=some_routing_payload)

    # Stream an async generator as message chunks
    async def token_stream():
        async for chunk in llm.astream(prompt):
            yield chunk

    await thread.post(token_stream())
```

### Message

Normalized inbound message. Exposes sender identity and text content. Provides `reply` and `react` shortcuts.

```python
async def handle_message(message: Message):
    print(message.text)      # normalized text
    print(message.user.id)   # sender user ID

    await message.reply("Got it!")
    await message.react("thumbsup")
```

---

## Streaming Helpers

Use these inside any graph node that receives a `StreamWriter`. They emit events directly to the client during execution.

> **Note:** `emit_*` helpers and custom event models emit typed events into the LangGraph `custom` stream. These events are understood and converted to A2A protocol events only when the graph is served via `aion-server` (i.e. with `aion-server-langgraph` installed). When running standalone — for example via `langgraph dev` or in unit tests — the events appear as raw objects in the stream, which is useful for debugging but produces no A2A output.

```python
from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage, AIMessageChunk
from aion.langgraph import emit_message, emit_task_update, emit_file_artifact, emit_data_artifact

def my_node(state: dict, writer: StreamWriter):
    # Ephemeral notification — reaches client, not saved in task history
    emit_message(writer, AIMessage(content="Searching knowledge base..."), ephemeral=True)

    # Structured data artifact
    emit_data_artifact(writer, {"status": "success", "results": [...]}, name="analysis")

    # File artifact by URL
    emit_file_artifact(writer, url="https://example.com/report.pdf", mime_type="application/pdf")

    # File artifact by raw bytes
    emit_file_artifact(writer, data=pdf_bytes, mime_type="application/pdf", name="report.pdf")

    # Message + metadata in one event
    emit_task_update(writer, message=AIMessage(content="Done"), metadata={"progress": 100})

    return state
```

### Streaming chunks

`AIMessageChunk` emitted via `emit_message` produces `STREAM_DELTA` artifacts (identical to LangGraph's automatic `messages` stream mode):

```python
def streaming_node(state: dict, writer: StreamWriter):
    for chunk in llm.stream(prompt):
        emit_message(writer, chunk)
    return state
```

---

## Client Events Reference

| Trigger | Client receives |
|---|---|
| LLM output chunk (automatic via `messages` stream) | `TaskArtifactUpdateEvent(STREAM_DELTA, last_chunk=false)` |
| `emit_message(AIMessage)` | `TaskStatusUpdateEvent(working, message=...)` |
| `emit_message(AIMessage, ephemeral=True)` | `TaskArtifactUpdateEvent(EPHEMERAL_MESSAGE)` |
| `emit_message(AIMessageChunk)` | `TaskArtifactUpdateEvent(STREAM_DELTA)` |
| `emit_task_update` | `TaskStatusUpdateEvent(working, message=..., metadata=...)` |
| `emit_file_artifact` / `emit_data_artifact` | `TaskArtifactUpdateEvent` |
| `thread.reply` / `thread.post` | `TaskStatusUpdateEvent(working, message=...)` |

For the complete mapping reference, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).
