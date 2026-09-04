# aion-server-langgraph

Server-side LangGraph integration for Aion. Implements `AgentPluginProtocol` — adapts inbound A2A requests into LangGraph `graph.astream()` invocations and maps graph outputs back into A2A Messages, Tasks, and streaming events. Discovered automatically by `aion-server` when installed.

---

## Setup

Add `framework: "langgraph"` to any agent in `aion.yaml`. The path must resolve to a `StateGraph` instance, a compiled `Pregel`, or a callable that returns one:

```yaml
aion:
  agents:
    my_agent:
      path: "./agent.py:build_graph"
      framework: "langgraph"
```

See [Quickstart](https://docs.aion.to/sdk/python/quickstart-langgraph) for a full working example.

---

## Inbound

When an inbound A2A `Message` arrives, the server populates the `messages` state field (if declared) and passes the full A2A context to the graph via LangGraph's runtime context mechanism.

### `messages` — LangChain message history

If the graph declares a `messages` field, the server injects a `HumanMessage` derived from the text parts of the inbound A2A message:

```python
from typing import TypedDict
from langchain_core.messages import BaseMessage

class State(TypedDict):
    messages: list[BaseMessage]
```

### Runtime context — A2A envelope

The full A2A context is passed to the graph via LangGraph's native runtime context and is
accessible through `AionRuntimeContext`. This includes the raw inbox, optional typed event, and
parsed `distribution_extension_payload` with distribution, behavior, environment, principal identity,
and service identity accessors. The recommended way to consume Aion events is to add a
`create_event_router` node from `aion-authoring-langgraph`, which handles injection automatically
while leaving graph edges explicit:

```python
from langgraph.graph import END, START
from aion.langgraph.authoring import create_event_router, Thread, Message

async def handle_message(thread: Thread, message: Message):
    await thread.reply(f"Got: {message.text}")

builder.add_node("aion_events", create_event_router(on_message=handle_message))
builder.add_edge(START, "aion_events")
builder.add_edge("aion_events", END)
```

For lower-level access, declare `runtime` or `context` in a handler signature — see [aion-authoring-langgraph](../aion-authoring-langgraph/README.md) for the full list of injectable parameters.

---

## Outbound — `a2a_outbox`

Return `a2a_outbox` in the graph's output to send an explicit A2A response. The value must be an `A2AOutbox` instance — a Pydantic wrapper that ensures protobuf objects are serializable by LangGraph's checkpoint saver:

```python
from a2a.types import Message, Role, Part, TextPart
from aion.core.types import A2AOutbox

def my_node(state: State) -> State:
    return {
        "a2a_outbox": A2AOutbox(
            message=Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text="Done!"))]
            )
        )
    }
```

`A2AOutbox` accepts either `message` or `task`. Without `a2a_outbox`, the server falls back to the accumulated `STREAM_DELTA` text from the current run as the final response message. 

For full outbound precedence rules, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).

---

## Checkpointing

The plugin configures the graph checkpointer automatically:

- **PostgreSQL** — used when `aion-db` has an active pool. Runs in a dedicated schema (`aion_langgraph`) to avoid collisions with application tables.
- **In-memory** — fallback when no database is available (state is lost on process restart).

No configuration is required. Schema setup and migrations run on first startup.

---

## Interrupts and Resume

LangGraph `interrupt()` calls are mapped to `TaskStatusUpdateEvent(INPUT_REQUIRED)`. The client resumes the graph by sending a new A2A message to the same task, which is forwarded as a `Command(resume=...)` to LangGraph.

```python
from langgraph.types import interrupt

def review_node(state: State) -> State:
    user_input = interrupt("Please confirm the action.")
    # execution resumes here with user_input filled in
    return {**state, "confirmation": user_input}
```

---

## Plugin Discovery

`aion-server` discovers this plugin at startup via dynamic import:

```python
# aion-server resolves this automatically when aion-server-langgraph is installed
"aion.server_langgraph.LangGraphPlugin"
```

If `aion-server-langgraph` is not installed, the import is silently skipped and LangGraph support is unavailable. No configuration or registration is required.

---

## Architecture

```
aion-server
  └── discovers LangGraphPlugin (aion-server-langgraph)
        ├── LangGraphAdapter       — compiles graphs, creates executors
        ├── LangGraphExecutor      — orchestrates stream / resume lifecycle
        │   ├── StreamExecutor     — calls graph.astream(), yields A2A events
        │   ├── EventConverter     — maps LangGraph events → A2A protocol events
        │   └── ResultHandler      — produces terminal events from a2a_outbox or delta text
        ├── CheckpointerFactory    — creates PostgreSQL or memory checkpointer
        └── Converters             — bidirectional A2A ↔ LangChain content block conversion
```
