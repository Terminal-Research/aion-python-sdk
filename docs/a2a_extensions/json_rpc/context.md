# Context Extension

The Context extension provides JSON-RPC methods for retrieving conversation contexts and context lists from the A2A server.

## Methods

- [context/get](#contextget) - Retrieve a specific conversation context
- [contexts/get](#contextsget) - Retrieve a list of available contexts

## context/get

Retrieves a specific conversation context with its message history.

### Request

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "method": "context/get",
  "params": {
    "context_id": "string",
    "history_length": 10,
    "history_offset": 0
  }
}
```

### Parameters

- **`context_id`** (required): Context identifier
- **`history_length`** (optional): Number of recent tasks to be retrieved
- **`history_offset`** (optional): The offset starting with the most recent message

### Success Response

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "result": {
    // Conversation object
  }
}
```

The `result` field contains a [Conversation](../models.md#conversation) object with context data and message history.

### Error Response

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "Server error description",
    "data": {}
  }
}
```

### Usage Example

```python
# Request the latest 5 tasks from context "conversation-123"
request = {
    "id": "get-context-1",
    "jsonrpc": "2.0",
    "method": "context/get",
    "params": {
        "context_id": "conversation-123",
        "history_length": 5
    }
}
```

---

## contexts/get

Retrieves a list of available conversation contexts.

### Request

```json
{
  "id": "request-id", 
  "jsonrpc": "2.0",
  "method": "contexts/get",
  "params": {
    "history_length": 50,
    "history_offset": 0
  }
}
```

### Parameters

- **`history_length`** (optional): Number of recent contexts to be retrieved
- **`history_offset`** (optional): The offset starting with the most recent context from which the server should start returning history

### Success Response

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "result": {
    // ContextsList object
  }
}
```

The `result` field contains a [ContextsList](../models.md#contextslist) object with an array of context identifiers and metadata.

### Error Response

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "Server error description",
    "data": {}
  }
}
```

### Usage Example

```python
# Request the 20 most recent contexts
request = {
    "id": "list-contexts-1", 
    "jsonrpc": "2.0",
    "method": "contexts/get",
    "params": {
        "history_length": 20
    }
}
```

---

## Implementation Notes

- Both methods support pagination through `history_length` and `history_offset` parameters
- Responses follow standard A2A data models:
  - `context/get` returns a [Conversation](../models.md#conversation) object
  - `contexts/get` returns a [ContextsList](../models.md#contextslist) object
- Error handling follows JSON-RPC 2.0 specification
