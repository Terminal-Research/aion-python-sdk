# aion-authoring-langgraph

LangGraph authoring toolkit for Aion. Provides state helpers, streaming utilities, and event-routing primitives that graph authors import directly. Safe to install without pulling in any server or plugin machinery.

---

## Installation

```bash
pip install aion-authoring-langgraph
```

Or, if you are serving the agent with Aion:

```bash
pip install aion-sdk[langgraph]
```

---

## Models

Use `aion_chat_model` when you want LangGraph/LangChain model calls to flow
through Aion's OpenAI-compatible model proxy:

```python
from aion.langgraph.authoring import aion_chat_model

llm = aion_chat_model("model-id-from-control-plane")
```

The helper calls LangChain's `init_chat_model` with `model_provider="openai"`,
`base_url="<AION_API_HOST>/v1"`, and an Aion JWT provider backed by the
configured `AION_CLIENT_ID` and `AION_CLIENT_SECRET`. Any other keyword
arguments are passed through to LangChain. Look up available model IDs in the
Aion control plane model catalog.

---

## MCP tools

Use `load_aion_mcp_tools` after an `AionRuntimeContext` is available to load
explicit MCP references and runtime-resolved capability MCP tools:

```python
from aion.api import (
    CapabilityReference,
    CapabilitySubjectSource,
    RuntimeCapabilityReference,
)
from aion.langgraph.authoring import load_aion_mcp_tools

tools = await load_aion_mcp_tools(
    context,
    capability_references=[
        CapabilityReference.global_mcp(),
    ],
    runtime_capability_references=[
        RuntimeCapabilityReference.mcp(key="mcp.twitter.distribution"),
        RuntimeCapabilityReference.primary_mcp(
            CapabilitySubjectSource.INCOMING_DISTRIBUTION
        ),
    ],
)
```

The helper builds LangChain `MultiServerMCPClient` configuration with Aion
bearer auth and an `Aion-Principal-Selector` derived from the runtime context.
Use `capability_references` for the SDK-level subject + kind + key shape, such
as the subjectless global metatools MCP server. Use
`runtime_capability_references` when the subject must be resolved from the
runtime request, such as a concrete MCP key on the active environment or the
primary MCP server for the incoming distribution.
Use `AionLangGraphMcpResolver` when you want to reuse the same MCP resolution
settings across invocations.

---

## Event Routing

Use `create_event_router` to create a normal LangGraph node that routes inbound A2A events to
handler functions based on event kind. Add the returned router with `builder.add_node(...)` and
connect it with ordinary LangGraph edges.

```python
from langgraph.graph import END, START, StateGraph
from aion.langgraph.authoring import create_event_router

builder = StateGraph(State)
builder.add_node(
    "aion_events",
    create_event_router(
        on_message=handle_message,
        on_reaction=handle_reaction,
        on_command=handle_command,
    ),
)
builder.add_edge(START, "aion_events")
builder.add_edge("aion_events", END)
```

Handlers receive only the parameters they declare — any subset is valid:

```python
async def handle_message(thread: Thread, message: Message, state: State):
    await thread.reply(f"Got your message: {message.text}")

async def handle_reaction(context: AionRuntimeContext, event):
    ...
```

Available injectable parameters: `state`, `runtime`, `context`, `event`, `distribution`,
`behavior`, `environment`, `principal_identity`, `service_identity`, `inbox`, `thread`,
`message`. Any other declared parameter is forwarded to LangGraph for native injection
(e.g. `config`, `store`).

Because the event router is just a node, applications can route into it from their own
entry nodes:

```python
builder.add_node("request_router", request_router)
builder.add_node("daemon", daemon_node)
builder.add_node("aion_events", create_event_router(on_message=handle_message))

builder.add_edge(START, "request_router")
builder.add_conditional_edges(
    "request_router",
    route_request,
    {
        "events": "aion_events",
        "daemon": "daemon",
    },
)
builder.add_edge("aion_events", END)
builder.add_edge("daemon", END)
```

---

## State Helpers — `Thread` and `Message`

`Thread` and `Message` are high-level wrappers over the raw A2A inbox. They are injected
automatically when declared in a handler registered via `create_event_router`.

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
from aion.langgraph.authoring import emit_message, emit_task_update, emit_file_artifact, emit_data_artifact

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
