# aion.adk.server

Aion server-side Google ADK integration — implements `AgentPluginProtocol` for the Aion Server runtime.

## Overview

This subpackage provides the server-side plugin for running Google ADK agents within the Aion platform. It handles:

- **Plugin & Adapter** — `ADKPlugin` / `ADKAdapter` implementing `AgentPluginProtocol` / `AgentAdapter`
- **Execution** — `ADKExecutor` / `ADKStreamExecutor`, running the agent through ADK's own `Runner`
- **Session management** — Memory and PostgreSQL backends via `SessionServiceFactory`
- **Artifact storage** — Memory and A2A-backed artifact service via `ArtifactServiceFactory`
- **Transformers** — Bidirectional A2A ↔ ADK format conversion

An agent's `path` in `aion.yaml` names an ADK agent (a `BaseAgent` instance) or a callable that returns one. The callable may be an `async def`: the server awaits it once at startup, on its own event loop, so a factory can load MCP tools or open a connection while it builds the agent.

## How an agent runs

The server runs an agent the way any ADK application does: through ADK's `Runner` (`Runner.run_async`). The Runner picks the agent that answers a message, resumes a paused invocation, writes the user's message and the agent's events to the session, and drives the agent; the server repeats none of that logic, so an agent behaves here as ADK documents it.

The Runner works only with what the server gives it (`execution/runner.py`):

- **Sessions** are the server's session service, memory or PostgreSQL. Every write, the Runner's included, passes one point where the turn in progress decides what a session keeps (`TurnSessionService`).
- **Artifacts** are the server's artifact service.
- **Nothing else is passed**: no memory service, no credential service, no plugins, no context cache, no event compaction. The Runner creates no session on its own; the server finds or creates it first.
- **The run configuration is fixed**: text out, streamed (`RUN_CONFIG`), and every invocation resumable.
- **The invocation context** is `AionInvocationContext`, which carries the Aion runtime context of the request.

After `transfer_to_agent` the next message of the context goes to the agent that answered last, as long as that agent may hand the conversation back to its parent (`disallow_transfer_to_parent` is off for it and for every agent above it); otherwise the root agent answers. That is the Runner's rule, unchanged.

## Inbound and outbound

The inbound A2A message becomes the invocation's `user_content`, part by part and in order: text as text, inline bytes as `inline_data` and a URL as `file_data`, each with its media type, and a structured data part as a text part holding its JSON. The part's metadata is not passed on.

On a deployment that stores files, a file is `file_data` with the file's address, and that is what the session keeps. A model is sent the content: `aion_lite_llm` replaces every part that carries a file by its `http(s)` address with `inline_data` in each request, and an agent with a model of its own sets `before_model_callback=inline_files_before_model` (`aion.adk.authoring.files`). The address is a download link of the deployment's storage with a limited lifetime, which a provider cannot be relied on to fetch. The content comes from memory when this server stored or read the file before, and from storage otherwise, for as long as the file is kept; `AION_FILE_CACHE_BYTES` (default 256 MiB) and `AION_FILE_CACHE_TTL` (seconds, default 3600) size that memory. Every file of the request is sent on every call. A file that cannot be read, or is larger than `AION_MODEL_FILE_MAX_BYTES` (default 20 MiB), fails the task and says so. A message may name a file by any address, so a file is downloaded from public addresses only, every redirect included: a host that resolves to a private, loopback, link-local or other non-public address is refused, and the connection goes to the address that was checked (`aion.core.http.file_transport`). The Aion API host (`AION_API_HOST`) is always allowed; `AION_FILE_TRUSTED_HOSTS` lists other hosts, comma-separated, that an agent is meant to reach on an internal address. These downloads take no proxy from the environment. With `aion_lite_llm` every file travels in the body of the request. ADK's `LiteLlm` puts an image, audio, video or text file there itself; any other document it would upload to the provider's Files API on every request when the model is OpenAI's, so `aion_lite_llm` hands it a text mark instead and its client puts the document into the finished request in the mark's place - Aion's model service is not a provider's Files API, and a document sent with the request leaves no copy behind. A request that lost a mark is not sent: the task fails and names the file. With a model class of the agent's own, how the content travels after `inline_files_before_model` is that class's.

What the model says reaches the client as it says it: partial events stream as deltas, and the non-partial event that closes a model call is the reply. Function calls, function responses and thoughts are not sent, and neither is code the model ran — `executable_code` and `code_execution_result` parts are left out of a reply. An agent that wants the client to see them sends them explicitly, as an artifact (`tool_context.save_artifact`), where they arrive as a data part. ADK's `Runner` writes every event the agent yields to the session before the agent resumes, so the next model call is built from a session that already holds the function response it follows.

A session belongs to one agent and one user: it is keyed `app_name` = the Aion agent id, `user_id` = the owner the agent's `owner_resolver` names for the request's `ServerCallContext` - by default its trusted user, and always the tasks table's `owner_scope`, `session_id` = the A2A `context_id`. Two users, or two agents on one database, presenting the same `context_id` get two sessions; memory between turns stays with its owner. Sessions saved before this key - under the agent's display name and the shared `default-user` - cannot be attributed to a user: a turn on such a context fails with `LegacyStateError`, and the old session is left in place until it is migrated or removed. The artifact service's fallback to the tasks table reads with the same agent and owner.

An agent has two ways to produce an artifact, side by side. `emit_artifact` (and `thread.post(artifact)`) publishes an A2A artifact as it is, as the LangGraph adapter does: every part, the artifact's own metadata, chunks of one artifact with `append`, and the `metadata` argument on the update event. It does not go through ADK's artifact service. ADK's own way, `tool_context.save_artifact`, keeps working as ADK documents it. The artifact service reads the context's task artifacts by name - through the tasks table with a database, through the turn's conversation history without one - so ADK's `load_artifacts` finds a published artifact too, as its first part: the one `Part` an ADK artifact holds; the whole artifact is in `aion.core.agent.conversation.find_artifact`. The latest version of a name is the newest one written in the current turn, saved or published, and before that the newest one the tasks hold. A version number asked for by name is ADK's own numbering of `save_artifact`; a published artifact carries the number SDK artifacts carry, if any, and several artifacts with different ids may share a name.

An artifact the agent saves (`tool_context.save_artifact`) is numbered per agent, user, context and name, from 0, and the number reaches the client as the artifact's `metadata.version`. With PostgreSQL, every number is recorded in `adk_artifact_versions` as it is given, so the servers of an agent never give one number twice: a save whose number another server took meanwhile numbers again from what the database holds, and fails with `ArtifactVersionConflictError` after ten such tries. Deleting the context removes the recorded numbers with its tasks.

A task has one streamed answer at a time, so agents that stream at once - the sub-agents of a `ParallelAgent` - take turns; a stream is told by its events' author and branch. The agent that started first streams live; the events of the others are held, and each follows as a section of its own once the one before it has ended, in the order they started. Every answer reaches the client whole. The task keeps the section that ended last, as it does after model calls made one after another: that is the agent that *started* last, whichever finished first. A `ParallelAgent` whose sub-agents each stream an answer should be followed by an agent that gives the turn's answer. A thought, a reaction or an artifact published as it is goes out at once, and so does the whole answer of an agent that does not stream, as a message beside the open section.

An event the agent yields keeps the author, branch and invocation ADK gave it: ADK shows a model another author's words as quoted data rather than as its own, and leaves another branch's events out. An event with no author or invocation - an agent written by hand may leave them empty - is given the turn's. Only events the SDK emits for the agent - through `Thread` or the `emit_*` helpers - are stamped as the agent's the Runner started the turn with: the root agent, or the sub-agent that took the conversation over on an earlier turn.

`a2a_outbox` answers the run that wrote it: the executor takes it from the run's own state deltas, and the value the session keeps afterwards is not applied to later turns of the same context.

## Asking the user

Every invocation is resumable, as in an ADK `App` with `ResumabilityConfig(is_resumable=True)`. A run that makes a long-running tool call stops there, and the task waits in `INPUT_REQUIRED`: `request_input` (`google.adk.tools`), `tool_context.request_confirmation`, a `LongRunningFunctionTool`, or a custom agent's own long-running call. To a client this looks the way a LangGraph `interrupt()` does - one text part asking the question, and the call's id as the message's `interruptId`. The question is the `message` of `request_input`, the confirmation's `hint`, or "Agent requires input" for a call that names none.

The user answers with an ordinary message, and the server turns it into the function response the call waits for, as ADK's command line does: for a confirmation, "y", "yes", "true" or "confirm" confirms and anything else declines; otherwise a JSON object is the response, other JSON or plain text is `{"result": ...}`, and a structured data part is the response as it is. The invocation the call belongs to runs on, resumed by ADK's `Runner`: by the root agent when the root agent started it, so a `SequentialAgent` or `LoopAgent` goes on from the sub-agent that asked and runs the ones after it; by the agent that made the call when the invocation started in a sub-agent.

A request for credentials (`tool_context.request_credential`) fails the task with a reason saying so: the server cannot take credentials yet.

With a file storage backend configured, an inline file part (`Part(raw=...)`, or a `data:` URL) anywhere in the outbox is stored and replaced with a URL part before the response is delivered, as every outbound file is; a file that cannot be stored fails the task. The session state still holds the outbox as the agent wrote it, bytes included; to keep file content out of session state, send the file with `thread.post(file_artifact(...))` instead.

## Failing a task

An agent, tool or callback that raises ends the turn with the task `FAILED`, and the status message says why, the same on the stream, in the unary answer and in a later `tasks/get`. To give the caller a reason of your own, raise `aion.core.exceptions.TaskFailedError("...")`; its text is the message, as written. An SDK error that names something the deployment lacks, such as a daemon identity, gives its own sentence the same way. Any other exception is a crash: the task says the agent stopped on an unexpected error and names the task to look up in the log, and the exception's text stays in the log.

A model response that ends with an error fails the task as well. ADK's models do not raise for a response that was blocked, cut short by the token limit or malformed: the event carries an `error_code` and, when the model gave one, an `error_message`, alone or beside the text that arrived before it. The run goes to its end as it does under ADK's `Runner`, and the task then closes `FAILED` with that code and message as its reason. The text streamed before the error stays in the task as its `aion:stream-delta` artifact and is not its answer.

## Installation

```bash
pip install "aionto-sdk[adk-server]"
```

The extra brings Google ADK, the authoring toolkit (`aion.adk.authoring`) and the server. `aion.server` finds this plugin on its own; without the extra it is skipped, and the skip names the extra to install.
