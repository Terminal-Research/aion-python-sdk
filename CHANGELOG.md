# Changelog

## Unreleased

### ⚠ BREAKING CHANGES

* **files:** a stored file's part keeps the version's address, which works only with authorization, instead of a download link: a task, a checkpoint and a session hold no credential. The server puts a download link in its answers and push notifications on behalf of the caller's usage attribution, and an agent reads a file with `read()` or `link()` of `ConversationFile`, or through `inline_files`; code that fetches `part.url` itself, or hands it to a model, gets no file.

### Bug Fixes

* **server:** a turn that ends the task with a `Task` in the outbox (`COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`) sends its push notification with that Task and ends its execution; the callback received only `SUBMITTED`, and the task stayed held by the server.
* **langgraph:** a state field declared as `Optional[A2AOutbox]` or `A2AOutbox` is kept out of the checkpoint, as `A2AOutboxField` is: the server gives it LangGraph's untracked channel in the graph it serves, so LangGraph no longer warns that it will refuse to load `aion.core.a2a.models.A2AOutbox`. A compiled graph the server copies keeps the msgpack allowlist LangGraph built from its state schema.
* **files:** download links of stored files are issued on behalf of the caller's usage attribution: the answer to a message and its stream with the message's, a read (`GetTask`, `ListTasks`, `GetContext`, `SubscribeToTask`, `CancelTask`) with the one it declares in `A2A-Extensions` and `Aion-Usage-Attribution`, and a turn's push notifications with that of the message that started it, so they carry working links, which the Files API refuses to issue for the deployment's daemon.
* **files:** a download link is handed out only on behalf of the usage attribution it was issued to, never to another caller or to the deployment's daemon, and a link refused to one of them no longer keeps another from asking for its own.
* **server:** `aion.server.tasks` and `aion.server.contexts` can be imported before any other module of the server; they failed with a circular import.

## 0.1.0 (2026-11-16)

The first final release of `aionto-sdk`. Changes are listed here from this
release on.
