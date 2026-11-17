# Changelog

## Unreleased

### ⚠ BREAKING CHANGES

* **files:** a stored file's part keeps the version's address, which works only with authorization, instead of a download link: a task, a checkpoint and a session hold no credential. The server puts a download link in its answers and push notifications on behalf of the caller's usage attribution, and an agent reads a file with `read()` or `link()` of `ConversationFile`, or through `inline_files`; code that fetches `part.url` itself, or hands it to a model, gets no file.

### Bug Fixes

* **server:** a turn that ends the task with a `Task` in the outbox (`COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`) sends its push notification with that Task and ends its execution; the callback received only `SUBMITTED`, and the task stayed held by the server.
* **langgraph:** a state field declared as `Optional[A2AOutbox]` or `A2AOutbox` is kept out of the checkpoint, as `A2AOutboxField` is: the server gives it LangGraph's untracked channel in the graph it serves, so LangGraph no longer warns that it will refuse to load `aion.core.a2a.models.A2AOutbox`. A compiled graph the server copies keeps the msgpack allowlist LangGraph built from its state schema.
* **files:** download links of stored files are issued on behalf of the caller's usage attribution: the answer to a message and its stream with the message's, a read (`GetTask`, `ListTasks`, `GetContext`, `SubscribeToTask`, `CancelTask`) with the one it declares in `A2A-Extensions` and `Aion-Usage-Attribution`, and a turn's push notifications with that of the message whose turn it is, so they carry working links, which the Files API refuses to issue for the deployment's daemon.
* **files:** a download link is handed out only on behalf of the usage attribution it was issued to, never to another caller or to the deployment's daemon, and a link refused to one of them no longer keeps another from asking for its own.
* **server:** `aion.server.tasks` and `aion.server.contexts` can be imported before any other module of the server; they failed with a circular import.
* **files:** a stored file the storage does not make available to the sender of a turn - one another participant of a shared conversation stored, say - no longer fails every turn that has it in its history: `read()` and `link()` of `ConversationFile` and the conversation's reads raise `ConversationFileForbiddenError` without fetching its address, and the SDK's models and `inline_files` leave it out with `[File "a.pdf" (application/pdf) is not available to this sender and was left out]`.
* **files:** a file whose address refuses a read is reported with what the address answered - it refused access, or no longer serves the file - instead of a download link that may have expired.
* **server:** the push notifications of a turn ask for file links on behalf of the request whose turn it is, a message queued into a running task included, instead of the request that started the task's execution on the server.
* **server:** a cancel runs with the usage attribution its request declares and sends, so what the executor does while cancelling - reading or storing files - is done on the canceller's behalf instead of the deployment's daemon.
* **files:** a part an agent sends with a stored file's address and no `aion:file` reference - an ADK `file_data` part, a LangGraph URL block - gets the reference back, so the client receives a working download link instead of an address that never downloads.

## 0.1.0 (2026-11-16)

The first final release of `aionto-sdk`. Changes are listed here from this
release on.
