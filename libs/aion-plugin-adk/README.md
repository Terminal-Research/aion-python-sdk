# A2A ↔ ADK Mapping

This document describes how Aion Server adapts A2A requests to Google ADK and maps ADK events back into A2A Messages/Tasks and streaming events.

---

## 1. Inbound Messages

### 1.1 Agent Invocation

Both `message/send` and `message/stream` use the same execution path: the agent's `run_async()` is always driven as an async event stream.

- **`message/send` (blocking=true)** — collects all events and returns the final `Task`.
- **`message/send` (blocking=false)** — returns after the first event, continues processing in background (`status="working"`).
- **`message/stream`** — yields events as they arrive and streams them to the client via SSE.

### 1.2 Accessing Inbound Context — `ctx.a2a_inbox`

When an inbound A2A `Message` arrives, Aion Server makes it available through `ctx.a2a_inbox` on the invocation context:

```python
class MyAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        inbox = ctx.a2a_inbox
        ...
```

`a2a_inbox` contains:
- `task`: the current `Task`
- `message`: the inbound A2A `Message` (full object, including non-text parts)
- `metadata`: `SendMessageRequest`-level metadata (e.g. distribution/network, trace info)

---

## 2. Outbound Messages

Valid responses to an A2A `MessageSendRequest` are a **`Message`** or **`Task`**.

Aion Server constructs the response using the following precedence:

### (1) `a2a_outbox` (authoritative)

Set `a2a_outbox` in `event.actions.state_delta` to provide an explicit A2A response. It must contain either a **Message** or a **Task**:

```python
from a2a.types import Message, Task, TextPart, Part, Role
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

class MyAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        # Option 1: outbox as Message
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={
                "a2a_outbox": Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text="Done!"))],
                )
            })
        )

        # Option 2: outbox as Task (patch)
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={
                "a2a_outbox": Task(
                    history=[...],
                    artifacts=[...],
                    metadata={"my_key": "my_value"},
                )
            })
        )
```

Server-owned fields are enforced:
- `task_id` and `context_id` are set to the current values managed by Aion Server (developer values ignored).
- Canonical routing/identity metadata (e.g. `aion:network`, sender IDs) is server-controlled.

Behavior:
- If `a2a_outbox` is a **Message** → append to current Task history.
- If `a2a_outbox` is a **Task** → treat as a **patch** to the server's Task:
  - server merges/extends `history` and `artifacts`
  - provided `metadata` merges shallowly (server-controlled keys take precedence)

> If you need to return a comprehensive A2A response (e.g., `DataPart`s, rich metadata, multiple artifacts), use `a2a_outbox` rather than relying on the streaming fallback.

### (2) Fallback: streaming delta text

If no `a2a_outbox` is set and the stream ended while still in partial mode (no closing non-partial event followed), Aion Server emits the accumulated delta text as the final response message.

> **Note:** A non-partial event with content already emits a `TaskStatusUpdateEvent(working)` during streaming and resets the accumulator. The fallback only applies when the last event was partial and no non-partial event closed the stream.

---

## 3. Streaming

### 3.1 Partial Events — Real-time Text Streaming

Yield ADK events with `partial=True` to stream text chunks to the client in real time. Each chunk is forwarded as a transitory `STREAM_DELTA` artifact update:

```python
from google.adk.events import Event

class MyAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        # Stream text in chunks
        for chunk in generate_chunks():
            yield Event(author=self.name, content=..., partial=True)

        # Final non-partial event closes the stream
        yield Event(author=self.name, content=..., partial=False)
```

The `STREAM_DELTA` artifact is transitory — it is not persisted to the Task's durable state. The final response is determined by `a2a_outbox`, non-partial event content, or accumulated delta text.

### 3.2 Artifacts

Use `ctx.artifact_service` to save file or data artifacts. Saved artifacts are automatically forwarded to the client as `TaskArtifactUpdateEvent`:

```python
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types

class MyAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        artifact = types.Part(
            inline_data=types.Blob(mime_type="application/pdf", data=pdf_bytes)
        )
        version = await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename="report.pdf",
            artifact=artifact,
        )

        # Declare the saved artifact in the event so it is forwarded to the client
        yield Event(
            author=self.name,
            actions=EventActions(artifact_delta={"report.pdf": version}),
        )
```

#### Artifact namespaces

| Filename prefix | Scope |
|---|---|
| `user:...` | User-scoped — shared across all sessions for this user |
| *(none)* | Session-scoped — private to the current context |

---

## 4. Client-side Events Reference

### Streaming

`STREAM_DELTA` is a transitory artifact — it is not persisted to the Task's durable state. The client uses it for live display only.

| Event | Client receives |
|---|---|
| Partial event | `TaskArtifactUpdateEvent(STREAM_DELTA, append=..., last_chunk=False)` |
| Non-partial with content | `TaskArtifactUpdateEvent(STREAM_DELTA, last_chunk=True)` + `TaskStatusUpdateEvent(working, message=...)` |
| Non-partial without content | `TaskArtifactUpdateEvent(STREAM_DELTA, last_chunk=True)` |

> The STREAM_DELTA close events are only emitted if at least one partial event was sent before.

### Artifacts

| Event | Client receives |
|---|---|
| Saved artifact | `TaskArtifactUpdateEvent` per artifact |

### Outbox

| Event | Client receives |
|---|---|
| `a2a_outbox` as Message | `TaskStatusUpdateEvent(working, message=...)` |
| `a2a_outbox` as Task patch | `TaskStatusUpdateEvent(working)` *(if metadata)* + N×`TaskStatusUpdateEvent(working, message=...)` + M×`TaskArtifactUpdateEvent` |

### Terminal

Every execution ends with exactly one terminal event.

| Event | Client receives |
|---|---|
| Agent finishes (no outbox) | `TaskStatusUpdateEvent(working, delta text)` *(only if delta_text non-empty)* + `TaskStatusUpdateEvent(completed)` |
| Agent finishes (with outbox) | `...` + `TaskStatusUpdateEvent(completed)` |
| Unhandled exception | `TaskStatusUpdateEvent(failed)` |

---

## 5. Summary

- Read `ctx.a2a_inbox` to access the inbound A2A Task, Message, and metadata.
- Yield partial events for real-time text streaming to the client.
- Use `ctx.artifact_service` to save artifacts; declare them in `event.actions.artifact_delta`.
- Optionally set `a2a_outbox` in `event.actions.state_delta` for full-fidelity A2A responses (Message or Task patch).
