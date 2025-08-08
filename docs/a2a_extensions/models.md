# A2A Extensions Data Models

This document defines the common data structures used across A2A protocol extensions.

## Models

- [Conversation](#conversation) - Complete conversation context with history and artifacts
- [ContextsList](#contextslist) - List of available context identifiers
- [ConversationTaskStatus](#conversationtaskstatus) - Task lifecycle status information

---

## Conversation

Data model representing a complete conversation context including message history, artifacts, and status.

- **`history`** - Sequential messages in the conversation (user/agent exchanges) - `List[a2a.Message]`
- **`artifacts`** - Generated content like code, documents, images, or files created during the conversation -
  `List[a2a.Artifact]`
- **`context_id`** - Unique identifier for this conversation - `str`
- **`status`** - Current task state - [ConversationTaskStatus](#conversationtaskstatus)

```json
{
  "context_id": "conversation-123",
  "history": [
    {
      "id": "msg-1",
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Hello, how are you?"
        }
      ]
    },
    {
      "id": "msg-2",
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "I'm doing well, thank you!"
        }
      ]
    }
  ],
  "artifacts": [],
  "status": {
    "state": "completed"
  }
}
```

---

## ContextsList
```
!! structure will be updated soon !!
```
A list of context identifiers representing available conversations.

- Each string is a unique `context_id` that can be used with `context/get` method

```json
[
  "conversation-123",
  "conversation-456",
  "conversation-789"
]
```

---

## ConversationTaskStatus

Status information about the current state of a conversation task.

- **`state`** - Current lifecycle state of the task (e.g., "pending", "running", "completed", "failed")

```json
{
  "state": "completed"
}
```