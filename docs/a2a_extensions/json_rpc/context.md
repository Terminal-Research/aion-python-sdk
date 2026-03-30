# Context Extension

The Context extension provides JSON-RPC methods for retrieving conversation contexts and context lists from the A2A server.

## Methods

- [GetContext](#getcontext) - Retrieve a specific conversation context
- [GetContexts](#getcontexts) - Retrieve a list of available contexts

## GetContext

Retrieves a specific conversation context with its message history.
````
### Reque``st

```json
{
  "id": "request-id",
  "jsonrpc": "2.0",
  "method": "GetContext",
  "params": {
    "contextId": "string",
    "historyLength": 10,
    "historyOffset": 0
  }
}
```

### Parameters

- **`contextId`** (required): Context identifier
- **`historyLength`** (optional): Number of recent tasks to be retrieved
- **`historyOffset`** (optional): The offset starting with the most recent message

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
    "method": "GetContext",
    "params": {``
        "contextId": "conversation-123",
        "historyLength": 5
    }
}
```

---

## GetContexts

Retrieves a list of available conversation contexts.

### Request

```json
{
  "id": "request-id", 
  "jsonrpc": "2.0",
  "method": "GetContexts",
  "params": {
    "historyLength": 50,
    "historyOffset": 0
  }
}
```

### Parameters

- **`historyLength`** (optional): Number of recent contexts to be retrieved
- **`historyOffset`** (optional): The offset starting with the most recent context from which the server should start returning history

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
    "method": "GetContexts",
    "params": {
        "historyLength": 20
    }
}
```

---

## Implementation Notes

- Both methods support pagination through `historyLength` and `historyOffset` parameters
- Responses follow standard A2A data models:
  - `GetContext` returns a [Conversation](../models.md#conversation) object
  - `GetContexts` returns a [ContextsList](../models.md#contextslist) object
- Error handling follows JSON-RPC 2.0 specification
