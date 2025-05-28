# A2A Protocol Server Requirements

This document outlines the tasks for building an Agent-to-Agent (A2A) protocol server that exposes LangGraph agents. The server should follow the A2A specification so that other clients and agents can communicate with it using standard JSON-RPC and HTTP endpoints.

## Functional Requirements

- **Agent Card**
  - Publish an Agent Card at `/.well-known/agent.json` describing the server, available skills, supported input/output modes, and authentication requirements.
  - Include optional capabilities such as `streaming` and `pushNotifications` when implemented.
- **RPC Methods**
  - Implement the A2A methods:
    - `message/send` and `message/stream` for sending messages to a LangGraph agent.
    - `tasks/get`, `tasks/cancel`, and `tasks/resubscribe` for task management.
    - `tasks/pushNotificationConfig/set` and `tasks/pushNotificationConfig/get` when push notifications are supported.
  - Use JSON-RPC 2.0 over HTTP(S) and return structured errors for invalid requests.
- **Task Lifecycle**
  - Create a unique task for each user request and track its status using the `TaskState` enum from the spec.
  - Store task history and generated artifacts for retrieval via the API.
- **Streaming**
  - Provide SSE streaming for long-running tasks when the client calls `message/stream` or `tasks/resubscribe`.
  - Emit `status-update` and `artifact-update` events as defined in the spec.
- **Push Notifications**
  - Allow clients to register a webhook URL for asynchronous updates.
  - Authenticate outgoing webhook requests according to the client-supplied configuration.
- **Authentication**
  - Require HTTP authentication (e.g., Bearer tokens) for API access. The Agent Card must advertise the accepted scheme.

## Non-Functional Requirements

- Use idiomatic asynchronous Python throughout the implementation.
- Document all public classes and functions with docstrings.
- Provide unit tests with `pytest` for task management, streaming behaviour, and push notifications.

## Step-by-Step Task List

1. **Define the Agent Card**
   - Create a JSON document that lists the server name, description, version, supported capabilities, and skills.
   - Host the file at `/.well-known/agent.json`.
2. **Set Up the HTTP Server**
   - Choose an async framework (e.g., FastAPI) and configure routes for each A2A RPC method.
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

