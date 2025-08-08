# A2A Extensions Documentation

This documentation provides a comprehensive reference for customizations and protocol extensions used in the
Agent-to-Agent (A2A) communication system.

## Organization

The documentation is organized into the following sections:

___

### Extensions

Formal protocol extensions that extend the base A2A communication capabilities. Each extension is documented with its
specific implementation details, message formats, and usage patterns.

#### JSON-RPC Extensions

- **[Context Extension](./json_rpc/context.md)** - Methods for retrieving conversation contexts and context lists (
  `context/get`, `contexts/get`)

___

### [Models](./models.md)

Data structure definitions and schemas used throughout the A2A communication protocol. These models define the format
and structure of messages, responses, and other data objects protocol-level data types.

___

### Other

Lower-level protocol features and implementation details that don't map directly to formal extensions but are essential
for understanding the complete A2A communication system.

#### Streaming

- **[Stream Delta Handling](./other/stream-delta-handling.md)** - Documentation for real-time response streaming through
  `TaskArtifactUpdateEvent` objects, including incremental content delivery, artifact reconstruction, and streaming
  lifecycle management.

___

## Purpose

This reference serves as the authoritative guide for:

- Protocol customizations and extensions
- Data models and message schemas
- Implementation-specific behaviors
- Integration patterns and best practices
- Streaming and real-time communication features

Each section contains detailed technical specifications, examples, and implementation guidance for developers working
with the A2A protocol.