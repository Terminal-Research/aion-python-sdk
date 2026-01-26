# A2A Extensions Documentation

This documentation provides a comprehensive reference for customizations and protocol extensions used in the
Agent-to-Agent (A2A) communication system.

## Quick Navigation

- **[JSON-RPC Methods](#json-rpc-extensions)** - Context retrieval and conversation management
- **[Data Models](#models)** - Protocol data structures and schemas
- **[Streaming](#streaming)** - Real-time response delivery patterns

---

## JSON-RPC Extensions

Formal protocol extensions that extend the base A2A communication capabilities with standard JSON-RPC 2.0 methods.

### Context Extension

**[Full Documentation](./json_rpc/context.md)**

Methods for retrieving conversation contexts and managing context lists:

- **`context/get`** - Retrieve a specific conversation context with message history
  - Parameters: `context_id`, `history_length`, `history_offset`
  - Returns: [Conversation](#conversation-model) object with full context and message history

- **`contexts/get`** - Retrieve a list of available contexts
  - Returns: [ContextsList](#contextslist-model) with all accessible context identifiers

---

## Models

**[Full Documentation](./models.md)**

Data structure definitions and schemas used throughout the A2A protocol extensions:

### Conversation Model
Complete conversation context including message history, artifacts, and task status.

**Fields:**
- `context_id` - Unique conversation identifier
- `history` - Sequential messages (user/agent exchanges)
- `artifacts` - Generated content (code, documents, files)
- `status` - Current task state

### ContextsList Model
List of available context identifiers for discovery and enumeration.

### ConversationTaskStatus Model
Task lifecycle status information for conversation tracking.

### A2AManifest Model
Root-level service manifest with API version and agent endpoint information.

---

## Streaming

Lower-level protocol features for real-time communication:

### Stream Delta Handling

**[Full Documentation](./other/stream-delta-handling.md)**

Real-time response streaming through `TaskArtifactUpdateEvent` objects:

- Incremental content delivery for large responses
- Artifact reconstruction and streaming lifecycle management
- Efficient bandwidth usage for real-time agent responses

---

## Purpose

This reference serves as the authoritative guide for:

- Protocol customizations and extensions
- Data models and message schemas
- Implementation-specific behaviors
- Integration patterns and best practices
- Streaming and real-time communication features

Each section contains detailed technical specifications, examples, and implementation guidance for developers working
with the A2A protocol.