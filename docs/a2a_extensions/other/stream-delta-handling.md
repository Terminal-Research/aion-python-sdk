# Stream Delta Handling

Aion streams partial agent text as A2A `TaskArtifactUpdateEvent` objects. Each
incremental chunk is carried in the reserved `aion:stream-delta` artifact so
clients can render partial output while the task is still running.

## Reserved Artifact

Use the current Aion artifact constants when producing or consuming stream
updates:

| Field | Value |
|---|---|
| `artifact.artifactId` | `aion:stream-delta` |
| `artifact.name` | `Stream Delta` |
| `artifact.metadata.status` | `active` while streaming, `finalized` when closed |
| `artifact.metadata.status_reason` | `chunk_streaming`, `complete_message`, `complete_task`, or `interrupted` |

LangGraph and ADK adapters emit stream deltas from partial model output. For
LangGraph, `AIMessageChunk` values become stream-delta artifact updates. The
first emitted chunk opens a section with `append: false`; later chunks normally
use `append: true`. A later update with `append: false` or a missing `append`
flag starts a new section for that artifact unless the producer explicitly marks
the update as a finalized reconstruction. This allows multiple thinking or
response sections to be displayed in order while still permitting final
full-value de-duplication.

## TaskArtifactUpdateEvent Shape

A streaming chunk is delivered as a `TaskArtifactUpdateEvent`:

```json
{
  "kind": "artifact-update",
  "taskId": "{task-id}",
  "contextId": "{context-id}",
  "append": false,
  "lastChunk": false,
  "artifact": {
    "artifactId": "aion:stream-delta",
    "name": "Stream Delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming",
      "https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0": {
        "schema": "https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0#StreamDeltaPayload"
      }
    },
    "parts": [
      {
        "content": {
          "$case": "text",
          "value": "content chunk"
        },
        "mediaType": "text/plain"
      }
    ]
  }
}
```

## Key Fields

- **`append`**:
  - `false` or absent - Open a new stream-delta section for this task and
    artifact. If another section has already been displayed for the task, insert
    a visual break before the new section.
  - `true` - Append this chunk to the current section for this task and artifact.

- **`lastChunk`**:
  - `false` while more stream events may follow.
  - `true` when the adapter is closing this stream-delta sequence.

- **`artifact.metadata.status`**:
  - `active` - Streaming is in progress.
  - `finalized` - The stream-delta artifact has been closed.

- **`artifact.metadata.status_reason`**:
  - `chunk_streaming` - The artifact contains an incremental content chunk.
  - `complete_message` - Streaming completed with a final message.
  - `complete_task` - Streaming completed with a terminal task update.
  - `interrupted` - Streaming was interrupted before completion.

## Streaming Example

This is the event sequence for streaming `Hello World!`.

### 1. Initial Chunk

```json
{
  "kind": "artifact-update",
  "append": false,
  "lastChunk": false,
  "artifact": {
    "artifactId": "aion:stream-delta",
    "name": "Stream Delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "content": {
          "$case": "text",
          "value": "Hello"
        },
        "mediaType": "text/plain"
      }
    ]
  }
}
```

### 2. Incremental Chunk

```json
{
  "kind": "artifact-update",
  "append": true,
  "lastChunk": false,
  "artifact": {
    "artifactId": "aion:stream-delta",
    "name": "Stream Delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "content": {
          "$case": "text",
          "value": " World!"
        },
        "mediaType": "text/plain"
      }
    ]
  }
}
```

### 3. Closed Stream

Adapters may close an open stream-delta artifact when the task reaches a final
message or terminal task state:

```json
{
  "kind": "artifact-update",
  "append": true,
  "lastChunk": true,
  "artifact": {
    "artifactId": "aion:stream-delta",
    "name": "Stream Delta",
    "metadata": {
      "status": "finalized",
      "status_reason": "complete_message"
    },
    "parts": []
  }
}
```

## Section Resets

A producer may start a new displayed section by sending an update with
`append: false` or by omitting `append`. This applies even when the new section
uses the same artifact ID as the previous section. Clients should preserve the
previous section, insert a visual break, and stream the new section from the new
parts.

A producer may replace an existing stream-delta or thinking-delta section when
it sends a finalized full-value reconstruction for the same task and artifact.
This is useful when the producer sends a full final value after incremental
chunks. Clients should replace the prior section text instead of rendering a
duplicate section.

This can happen when:

- A previous stream was interrupted and later resumed.
- A new streaming sequence starts within the same task.
- The producer needs to replace partial output with corrected or reconstructed
  content.

Example new-section update:

```json
{
  "kind": "artifact-update",
  "append": false,
  "artifact": {
    "artifactId": "aion:stream-delta",
    "name": "Stream Delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "content": {
          "$case": "text",
          "value": "A new streamed section starts here..."
        },
        "mediaType": "text/plain"
      }
    ]
  }
}
```

## Client Guidance

Clients should special-case `artifact.artifactId === "aion:stream-delta"` for
live response text rendering. Aion Chat also recognizes
`artifact.artifactId === "aion:thinking-delta"` as live thinking text; it follows
the same append and section-reset behavior, but it is not treated as the final
copyable response.

When a final agent message is received after streaming, clients may replace the
current `aion:stream-delta` response section for that task with the final
message. If the task only produced `aion:thinking-delta` output and no response
stream-delta section, the final response should be rendered as a separate
response section.

Other artifact IDs may represent files, structured data, reactions, ephemeral
messages, cards, or extension-specific payloads and should not be merged into
the main streamed text unless the client explicitly supports that artifact type.
