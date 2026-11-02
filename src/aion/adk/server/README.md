# aion.adk.server

Aion server-side Google ADK integration — implements `AgentPluginProtocol` for the Aion Server runtime.

## Overview

This subpackage provides the server-side plugin for running Google ADK agents within the Aion platform. It handles:

- **Plugin & Adapter** — `ADKPlugin` / `ADKAdapter` implementing `AgentPluginProtocol` / `AgentAdapter`
- **Execution** — `ADKExecutor` / `ADKStreamExecutor` for streaming ADK agent runs
- **Session management** — Memory and PostgreSQL backends via `SessionServiceFactory`
- **Artifact storage** — Memory and A2A-backed artifact service via `ArtifactServiceFactory`
- **Transformers** — Bidirectional A2A ↔ ADK format conversion

## Inbound and outbound

The inbound A2A message becomes the invocation's `user_content`, part by part and in order: text as text, inline bytes as `inline_data` and a URL as `file_data`, each with its media type, and a structured data part as a text part holding its JSON. The part's metadata is not passed on.

What the model says reaches the client as it says it: partial events stream as deltas, and the non-partial event that closes a model call is the reply. Function calls, function responses and thoughts are not sent, and neither is code the model ran — `executable_code` and `code_execution_result` parts are left out of a reply. An agent that wants the client to see them sends them explicitly, as an artifact (`tool_context.save_artifact`), where they arrive as a data part. Every event the agent yields is in the session before the agent resumes, as under ADK's own `Runner`, so the next model call is built from a session that already holds the function response it follows.

A session belongs to one agent and one user: it is keyed `app_name` = the Aion agent id, `user_id` = the owner the agent's `owner_resolver` names for the request's `ServerCallContext` - by default its trusted user, and always the tasks table's `owner_scope`, `session_id` = the A2A `context_id`. Two users, or two agents on one database, presenting the same `context_id` get two sessions; memory between turns stays with its owner. Sessions saved before this key - under the agent's display name and the shared `default-user` - cannot be attributed to a user: a turn on such a context fails with `LegacyStateError`, and the old session is left in place until it is migrated or removed. The artifact service's fallback to the tasks table reads with the same agent and owner.

An artifact the agent saves (`tool_context.save_artifact`) is numbered per agent, user, context and name, from 0, and the number reaches the client as the artifact's `metadata.version`. With PostgreSQL, every number is recorded in `adk_artifact_versions` as it is given, so the servers of an agent never give one number twice: a save whose number another server took meanwhile numbers again from what the database holds, and fails with `ArtifactVersionConflictError` after ten such tries. Deleting the context removes the recorded numbers with its tasks.

An event the agent yields keeps the author, branch and invocation ADK gave it, as under ADK's own `Runner`: ADK shows a model another author's words as quoted data rather than as its own, and leaves another branch's events out. Only events the SDK emits for the agent - through `Thread` or the `emit_*` helpers - are stamped as the root agent's.

`a2a_outbox` answers the run that wrote it: the executor takes it from the run's own state deltas, and the value the session keeps afterwards is not applied to later turns of the same context.

## Asking the user

Every invocation is resumable, as in an ADK `App` with `ResumabilityConfig(is_resumable=True)`. A run that makes a long-running tool call stops there, and the task waits in `INPUT_REQUIRED`: `request_input` (`google.adk.tools`), `tool_context.request_confirmation`, a `LongRunningFunctionTool`, or a custom agent's own long-running call. To a client this looks the way a LangGraph `interrupt()` does - one text part asking the question, and the call's id as the message's `interruptId`. The question is the `message` of `request_input`, the confirmation's `hint`, or "Agent requires input" for a call that names none.

The user answers with an ordinary message, and the server turns it into the function response the call waits for, as ADK's command line does: for a confirmation, "y", "yes", "true" or "confirm" confirms and anything else declines; otherwise a JSON object is the response, other JSON or plain text is `{"result": ...}`, and a structured data part is the response as it is. The agent that made the call runs on in the invocation it belongs to.

A request for credentials (`tool_context.request_credential`) fails the task with a reason saying so: the server cannot take credentials yet.

With a file storage backend configured, an inline file part (`Part(raw=...)`) anywhere in the outbox is stored and replaced with a URL part before the response is delivered, as every outbound file is; a file that cannot be stored fails the task. The session state still holds the outbox as the agent wrote it, bytes included; to keep file content out of session state, send the file with `thread.post(file_artifact(...))` instead.

## Failing a task

An agent, tool or callback that raises ends the turn with the task `FAILED`, and the status message says why, the same on the stream, in the unary answer and in a later `tasks/get`. To give the caller a reason of your own, raise `aion.core.exceptions.TaskFailedError("...")`; its text is the message, as written. An SDK error that names something the deployment lacks, such as a daemon identity, gives its own sentence the same way. Any other exception is a crash: the task says the agent stopped on an unexpected error and names the task to look up in the log, and the exception's text stays in the log.

## Installation

```bash
pip install "aionto-sdk[adk-server]"
```

The extra brings Google ADK, the authoring toolkit (`aion.adk.authoring`) and the server. `aion.server` finds this plugin on its own; without the extra it is skipped, and the skip names the extra to install.
