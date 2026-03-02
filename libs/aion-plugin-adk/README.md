# aion-plugin-adk

Google ADK plugin for Aion Server. Integrates as an `AgentPluginProtocol` — adapts inbound A2A requests into ADK `run_async()` invocations and maps ADK events back into A2A Messages, Tasks, and streaming events.

---

## Setup

Add `framework: "adk"` to any agent in `aion.yaml`. The path must resolve to a `BaseAgent` instance or a factory that returns one:

```yaml
aion:
  agents:
    my_agent:
      path: "./agent.py:create_agent"
      framework: "adk"
```

See [Quickstart](https://docs.aion.to/sdk/python/quickstart-adk) for a full working example.

---

## Inbound — `ctx.a2a_inbox`

When an inbound A2A `Message` arrives, Aion Server makes it available on the invocation context:

```python
class MyAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        inbox = ctx.a2a_inbox
        task = inbox.task        # current A2A Task
        message = inbox.message  # full inbound Message, including non-text parts
        metadata = inbox.metadata  # SendMessageRequest-level metadata
```

---

## Outbound — `a2a_outbox`

Set `a2a_outbox` in `event.actions.state_delta` to return an explicit A2A response. Accepts a `Message` or a `Task` (treated as a patch to history, artifacts, and metadata):

```python
yield Event(
    author=self.name,
    actions=EventActions(state_delta={
        "a2a_outbox": Message(role=Role.agent, parts=[Part(root=TextPart(text="Done!"))])
    })
)
```

Without `a2a_outbox`, Aion Server falls back to accumulated partial text as the final response. For comprehensive responses (DataParts, rich metadata, multiple artifacts), always use `a2a_outbox`.

For full outbound precedence rules, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/adk/message-mapping).

---

## Streaming — partial events

Yield `partial=True` events to stream text chunks in real time. Each chunk is forwarded to the client as a transitory `STREAM_DELTA` artifact:

```python
for chunk in generate_chunks():
    yield Event(author=self.name, content=..., partial=True)

# Final non-partial event closes the stream
yield Event(author=self.name, content=..., partial=False)
```

`STREAM_DELTA` is not persisted — it is for live display only. The final response is determined by `a2a_outbox`, non-partial event content, or accumulated delta text.

For a full streaming example, see [Streaming API](https://docs.aion.to/sdk/python/frameworks/adk/streaming-api).

---

## Artifacts

Save artifacts via `ctx.artifact_service`, then declare them in `event.actions.artifact_delta` so Aion Server forwards them to the client as `TaskArtifactUpdateEvent`:

```python
version = await ctx.artifact_service.save_artifact(
    app_name=ctx.app_name, user_id=ctx.user_id,
    session_id=ctx.session.id, filename="report.pdf", artifact=artifact,
)
yield Event(author=self.name, actions=EventActions(artifact_delta={"report.pdf": version}))
```

Artifact scope is controlled by filename prefix:

| Prefix | Scope |
|---|---|
| `user:...` | User-scoped — shared across all sessions for this user |
| *(none)* | Session-scoped — private to the current context |

For storage backend configuration, see [Artifact Storage](https://docs.aion.to/sdk/python/frameworks/adk/artifact-storage).

---

## Client Events

Every execution ends with exactly one terminal event: `TaskStatusUpdateEvent(completed)` or `TaskStatusUpdateEvent(failed)`. Key events during execution:

| Trigger | Client receives |
|---|---|
| Partial event | `TaskArtifactUpdateEvent(STREAM_DELTA, last_chunk=false)` |
| Non-partial event with content | `TaskStatusUpdateEvent(working, message=...)` |
| Saved artifact | `TaskArtifactUpdateEvent` |
| `a2a_outbox` as Message | `TaskStatusUpdateEvent(working, message=...)` |

For the complete events reference, see [Events Reference](https://docs.aion.to/sdk/python/frameworks/adk/events-reference).
