# aion-plugin-langgraph

LangGraph plugin for Aion Server. Integrates as an `AgentPluginProtocol` — adapts inbound A2A requests into LangGraph `graph.astream()` invocations and maps graph outputs/events back into A2A Messages, Tasks, and streaming events.

---

## Setup

Add `framework: "langgraph"` to any agent in `aion.yaml`. The path must resolve to a `StateGraph` instance or a factory that returns one:

```yaml
aion:
  agents:
    my_agent:
      path: "./agent.py:build_graph"
      framework: "langgraph"
```

See [Quickstart](https://docs.aion.to/sdk/python/quickstart-langgraph) for a full working example.

---

## Inbound — `state.a2a_inbox`

When an inbound A2A `Message` arrives, Aion Server populates two state properties (if declared):

- **`messages`** — receives a `HumanMessage` derived from the text parts of the inbound A2A message.
- **`a2a_inbox`** — receives the full A2A envelope: `task`, `message`, and `metadata`.

```python
from typing import TypedDict
from langchain_core.messages import BaseMessage

class State(TypedDict):
    messages: list[BaseMessage]
    a2a_inbox: dict  # task, message, metadata
```

---

## Outbound — `a2a_outbox`

Return `a2a_outbox` in the graph's output dict to send an explicit A2A response. Accepts a `Message` or a `Task` (treated as a patch to history, artifacts, and metadata):

```python
from a2a.types import Message, Role, Part, TextPart

def my_node(state: State) -> State:
    return {
        "a2a_outbox": Message(
            role=Role.agent,
            parts=[Part(root=TextPart(text="Done!"))]
        )
    }
```

Without `a2a_outbox`, Aion Server falls back to the last `AIMessage` in `state.messages` as the final response. For comprehensive responses (DataParts, rich metadata, multiple artifacts), always use `a2a_outbox`.

For full outbound precedence rules, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).

---

## Streaming

LangGraph's `messages` stream mode automatically forwards LLM output chunks to the client as transitory `STREAM_DELTA` artifacts — no extra code required. For explicit control, use the `emit_*` helpers from `aion.langgraph`:

```python
from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage, AIMessageChunk
from aion.langgraph import emit_file_artifact, emit_data_artifact, emit_message, emit_task_update

def my_node(state: dict, writer: StreamWriter):
    # Ephemeral notification — sent to client, not saved in task history
    emit_message(writer, AIMessage(content="Searching knowledge base..."), ephemeral=True)

    # Emit a structured data artifact
    emit_data_artifact(writer, {"status": "success", "results": [...]}, name="analysis")

    # Emit a file artifact
    emit_file_artifact(writer, url="https://example.com/report.pdf", mime_type="application/pdf")

    # Emit message + metadata as one event
    emit_task_update(writer, message=AIMessage(content="Done"), metadata={"progress": 100})

    return state
```

For the full streaming API reference, see [Streaming API](https://docs.aion.to/sdk/python/frameworks/langgraph/streaming-api).

---

## Client Events

Every execution ends with exactly one terminal event: `TaskStatusUpdateEvent(completed)` or `TaskStatusUpdateEvent(failed)`. Key events during execution:

| Trigger | Client receives |
|---|---|
| LLM output chunk (automatic) | `TaskArtifactUpdateEvent(STREAM_DELTA, last_chunk=false)` |
| `a2a_outbox` as Message | `TaskStatusUpdateEvent(working, message=...)` |
| `emit_task_update` / `emit_message(AIMessage)` | `TaskStatusUpdateEvent(working, message=...)` |
| `emit_message(AIMessage, ephemeral=True)` | `TaskArtifactUpdateEvent(EPHEMERAL_MESSAGE)` |
| `emit_file_artifact` / `emit_data_artifact` | `TaskArtifactUpdateEvent` |

For the complete mapping reference, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).
