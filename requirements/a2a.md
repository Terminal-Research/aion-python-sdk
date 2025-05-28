# A2A Protocol Server Requirements

This document explains how to implement an Agent-to-Agent (A2A) protocol server for exposing LangGraph agents. The server must faithfully follow the A2A specification so that other agents or clients can communicate with it via standard JSON-RPC over HTTP.

## Functional Requirements

- **Agent Card**
  - Publish a JSON Agent Card at `/.well-known/agent.json` describing the server, its skills, supported input and output modes, and authentication requirements.
  - Advertise optional capabilities such as `streaming` (Server-Sent Events) and `pushNotifications` when implemented.
- **RPC Methods**
  - Support all A2A methods:
    - `message/send` and `message/stream` to deliver user messages to a LangGraph agent.
    - `tasks/get`, `tasks/cancel`, and `tasks/resubscribe` for task management.
    - `tasks/pushNotificationConfig/set` and `tasks/pushNotificationConfig/get` when push notifications are supported.
    - `agent/authenticatedExtendedCard` for retrieving an extended Agent Card after authentication.
  - Use JSON-RPC 2.0 request and response structures over HTTPS. Return well‑formed errors when requests fail validation.
- **Task Lifecycle**
  - Create a unique task for each request and track its status using the `TaskState` enumeration from the specification.
  - Persist task history and generated artifacts for later retrieval via the API.
- **Streaming**
  - Provide SSE streams when the client calls `message/stream` or `tasks/resubscribe`.
  - Send `status-update` and `artifact-update` events as defined in the spec.
- **Push Notifications**
  - Allow clients to register a webhook URL for asynchronous updates.
  - Authenticate outgoing webhook requests according to the client-supplied configuration.
- **Authentication**
  - Require HTTP authentication (for example Bearer tokens or API keys). The Agent Card must advertise the supported schemes.

## Non-Functional Requirements

- Use idiomatic asynchronous Python throughout the implementation.
- Document all public classes and functions with docstrings.
- Provide unit tests with `pytest` for task management, streaming behaviour, and push notifications.

## Step-by-Step Task List

1. **Define the Agent Card**
   - Create a JSON document listing the server name, description, version, supported capabilities, and skills.
   - Host the file at `/.well-known/agent.json`.
2. **Set Up the HTTP Server**
   - Use an async framework such as FastAPI and configure routes for each A2A RPC method.
   - Parse incoming JSON-RPC requests and dispatch them to handler functions.
3. **Implement Task Management**
   - Generate task IDs and context IDs for each request.
   - Persist task status, history, and artifacts.
4. **Integrate LangGraph**
   - Run LangGraph workflows when handling `message/send` or `message/stream` and update tasks accordingly.
5. **Add Streaming Support**
   - Use Server-Sent Events to stream `status-update` and `artifact-update` events.
6. **Add Push Notification Handling**
   - Accept push notification configurations from clients and send HTTP POST requests when tasks change state.
7. **Enforce Authentication**
   - Validate credentials on every request and return `401 Unauthorized` when missing or invalid.
8. **Write Tests**
   - Cover success and failure cases for RPC methods, streaming behaviour, and webhook notifications.

## A2A Protocol Overview

The A2A protocol defines how independent agents communicate over HTTP. All messages use JSON-RPC 2.0 payloads and are typically exchanged over HTTPS. Below is a reference of the major objects, states, and RPC methods needed to implement a compliant server.

### Core Objects

#### Agent Card
A JSON document that advertises the agent’s identity and capabilities. Important fields include `name`, `description`, `url` (the base URL for A2A methods), `version`, optional `documentationUrl`, `capabilities` (flags for streaming and push notifications), authentication `securitySchemes`, default input and output modes, and an array of supported `skills`.

`supportsAuthenticatedExtendedCard` signals that an authenticated client can fetch a more detailed card from `agent/authenticatedExtendedCard`.

#### Agent Skill
Represents a single capability the agent can perform. Each skill has an `id`, `name`, `description`, and set of `tags` for discovery. Skills may override default `inputModes` and `outputModes` and may include example prompts in `examples`.

#### Messages and Parts
A `Message` represents one conversational turn. Each message has a `role` (`user` or `agent`) and a list of `parts`. Parts are one of:
- `TextPart` – plain text content
- `FilePart` – file bytes or a URI
- `DataPart` – structured JSON
Messages also include `messageId`, optional metadata, optional `taskId`, and optional `contextId`.

#### Tasks and Artifacts
A `Task` is a stateful unit of work created when a message is processed. It includes a unique `id`, a `contextId` shared by related tasks, and a `status` field. Tasks may store `history` (recent messages) and `artifacts` (files or data produced by the agent). Each artifact contains an `artifactId`, optional name and description, and one or more `parts` just like messages.

#### Task Status and Task State
`TaskStatus` combines a `TaskState` value with an optional status message and timestamp. States include:
- `submitted`
- `working`
- `input-required`
- `completed`
- `canceled`
- `failed`
- `rejected`
- `auth-required`
- `unknown`
`completed`, `canceled`, `failed`, `rejected`, and `unknown` are terminal states.

#### PushNotificationConfig
Describes how the server should POST task updates to the client. It includes a target `url`, optional `token` for validation, and optional `authentication` block describing how the server must authenticate when calling the webhook.

### Authentication and Security

A2A relies on standard HTTP authentication methods. The Agent Card advertises which schemes are required, such as `Bearer` tokens, `Basic` auth, or `ApiKey`. Clients acquire credentials out-of-band and send them in HTTP headers for every request. Servers must authenticate each request and return `401` or `403` if authentication fails or is not authorized.

### RPC Methods

Each method is invoked via HTTP POST with a JSON-RPC body:

- **`message/send`** – Sends a message. The response is either a `Message` (for quick replies) or a `Task` representing ongoing work. Optional configuration allows the client to specify accepted output modes, request history length, push notification setup, or a blocking reply.
- **`message/stream`** – Sends a message and subscribes the caller to streaming updates over SSE. Each SSE event carries a JSON-RPC response with either a `Message`, a `Task`, a `TaskStatusUpdateEvent`, or a `TaskArtifactUpdateEvent`.
- **`tasks/get`** – Retrieves the current state of an existing task. Parameters include the task ID and an optional history length.
- **`tasks/cancel`** – Attempts to cancel a task that is in progress.
- **`tasks/resubscribe`** – Reconnects to a streaming SSE feed for a task after a disconnection.
- **`tasks/pushNotificationConfig/set`** – Sets or updates the webhook configuration for task notifications.
- **`tasks/pushNotificationConfig/get`** – Retrieves the current webhook configuration.
- **`agent/authenticatedExtendedCard`** (HTTP GET) – Returns a more detailed Agent Card once the client is authenticated and the server supports it.

### Error Handling

A2A uses JSON-RPC error codes. Standard codes include `-32700` (Parse error), `-32600` (Invalid Request), `-32601` (Method not found), `-32602` (Invalid params), and `-32603` (Internal error). The protocol also defines additional server errors in the `-32000` to `-32099` range such as `TaskNotFoundError` and `PushNotificationNotSupportedError`. Servers should include explanatory messages and may supply additional data in the error response.

### Streaming and Push Workflows

For long running tasks, servers with the `streaming` capability may deliver incremental updates using SSE. The initial response to `message/stream` is an HTTP 200 with `Content-Type: text/event-stream`; each event’s `data` field contains a JSON-RPC response. Clients may reconnect with `tasks/resubscribe` if the stream is interrupted.

If `pushNotifications` is supported, clients can provide a `PushNotificationConfig` via `message/send` or `tasks/pushNotificationConfig/set`. The server will then POST task updates to the configured webhook URL when the client is offline or prefers asynchronous delivery.

### Relationship to MCP

A2A focuses on agent-to-agent coordination, while the Model Context Protocol (MCP) standardizes how agents interact with tools and external APIs. A2A servers may internally use MCP to perform actions required to complete a task on behalf of the client.

This overview captures the structures, states, and RPC calls required to implement an A2A-compliant LangGraph server. Consult the full specification for detailed field descriptions and additional examples of streaming, push notifications, and error payloads.
