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
first emitted chunk opens or replaces the artifact with `append: false`; later
chunks normally use `append: true`.

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
  - `false` - Replace previous stream-delta content. This is used for the first
    chunk of a stream and for any full reconstruction of the artifact.
  - `true` - Append this chunk to the existing stream-delta content.

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

## Artifact Reconstruction

A producer may replace the stream-delta content by sending a new update with
`append: false`. Clients should treat this as a reset for the `aion:stream-delta`
artifact and rebuild displayed content from the new parts.

This can happen when:

- A previous stream was interrupted and later resumed.
- A new streaming sequence starts within the same task.
- The producer needs to replace partial output with corrected or reconstructed
  content.

Example reconstruction update:

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
          "value": "Completely new response based on resumed input..."
        },
        "mediaType": "text/plain"
      }
    ]
  }
}
```

## Client Guidance

Clients should special-case `artifact.artifactId === "aion:stream-delta"` for
live text rendering. Other artifact IDs may represent files, structured data,
reactions, ephemeral messages, cards, or extension-specific payloads and should
not be merged into the main streamed text unless the client explicitly supports
that artifact type.
