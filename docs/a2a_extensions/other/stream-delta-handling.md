# Streaming Response Documentation

The agent streams its response in real-time through a series of events. The streaming process uses
`TaskArtifactUpdateEvent` objects to deliver content incrementally, allowing clients to display partial responses as
they're generated.

### TaskArtifactUpdateEvent Structure

Each streaming chunk is delivered as a `TaskArtifactUpdateEvent` with the following key properties:

```json
{
  "kind": "artifact-update",
  "taskId": "{task-id}",
  "contextId": "{context-id}",
  "append": boolean,
  "lastChunk": boolean,
  "artifact": {
    "artifactId": "stream_delta",
    "name": "stream_delta",
    "metadata": {
      "status": "active"
      |
      "finalized",
      "status_reason": "chunk_streaming"
      |
      "complete_message"
      |
      "interrupt"
    },
    "parts": [
      {
        "kind": "text",
        "text": "content chunk"
      }
    ]
  }
}
```

### Key Fields

- **`append`**:
  - `false` - Replace previous content (first chunk or final complete message)
  - `true` - Append to previous content (incremental chunks)

- **`lastChunk`**: Always `false` during streaming (reserved for future use)

- **`artifact.metadata.status`**:
  - `"active"` - Streaming in progress
  - `"finalized"` - Complete message ready

- **`artifact.metadata.status_reason`**:
  - `"chunk_streaming"` - Incremental content delivery
  - `"complete_message"` - Final assembled message
  - `"interrupt"` - Streaming was interrupted by input-required event etc

## Streaming Example

Here's the sequence of events for streaming "Hello World!":

### 1. Initial Chunk (append: false)

```json
{
  "kind": "artifact-update",
  "append": false,
  "artifact": {
    "artifactId": "stream_delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "kind": "text",
        "text": "Hello"
      }
    ]
  }
}
```

### 2. Incremental Chunks (append: true)

```json
{
  "kind": "artifact-update",
  "append": true,
  "artifact": {
    "artifactId": "stream_delta",
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "kind": "text",
        "text": " World!"
      }
    ]
  }
}
```

### 3. Final Complete Message (append: false)

```json
{
  "kind": "artifact-update",
  "append": false,
  "artifact": {
    "artifactId": "stream_delta",
    "metadata": {
      "status": "finalized",
      "status_reason": "complete_message"
    },
    "parts": [
      {
        "kind": "text",
        "text": "Hello"
      },
      {
        "kind": "text",
        "text": " World!"
      }
    ]
  }
}
```

## Artifact Reconstruction

**Important**: After an artifact reaches the `"finalized"` status, it may be completely reconstructed during subsequent
streaming within the same task. This typically occurs when:

- The streaming was previously interrupted (e.g., requiring user input)
- A new streaming session begins within the same task context
- The agent continues processing after user interaction

### Reconstruction Process

When reconstruction occurs, the artifact content is rebuilt from scratch:

1. **New streaming session starts** - The artifact is reset and streaming begins anew
2. **Previous content is replaced** - All existing content is discarded
3. **Fresh content assembly** - New content is streamed and assembled according to the current agent output

### Example: Interrupted and Resumed Streaming

**Initial streaming (interrupted):**

```json
// Streaming A - interrupted
{
  "artifact": {
    "metadata": {
      "status": "finalized",
      "status_reason": "interrupt"
    },
    "parts": [
      {
        "kind": "text",
        "text": "Initial partial response..."
      }
    ]
  }
}
```

**After user input - reconstruction begins:**

```json
// New streaming session - content rebuilt from scratch
{
  "append": false,
  "artifact": {
    "metadata": {
      "status": "active",
      "status_reason": "chunk_streaming"
    },
    "parts": [
      {
        "kind": "text",
        "text": "Completely new response based on user input..."
      }
    ]
  }
}
```

This reconstruction ensures that the artifact always contains the most current and relevant content for the ongoing
task, while maintaining the streaming performance benefits.

## Additional Context

The streaming system is designed to provide immediate visual feedback while ensuring data integrity through the final
complete message delivery. This dual approach allows for responsive UX while maintaining reliable message completion
detection.

