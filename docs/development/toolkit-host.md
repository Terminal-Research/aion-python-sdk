# Toolkit host

A toolkit is a framework-agnostic package that does one kind of work for an
agent and plugs into any Aion server. The server is its host: it finds the
toolkit, routes the requests that activate the toolkit's A2A extension to it,
checks them before a task exists, drives the toolkit's turns and turns what
they yield into A2A. The toolkit never builds an A2A object and the server
holds no code for any particular toolkit.

| Where | What |
| --- | --- |
| `src/aion/core/toolkit/` | The protocol: `Toolkit`, `ToolkitDescriptor`, `ToolkitRequest`, `PreflightContext`, `ToolkitContext`, `ModelServiceAccess`, the values a toolkit yields (`models.py`) and the codes the server reserves (`codes.py`). It imports no a2a type. |
| `src/aion/server/agent/execution/extensions/toolkit/` | The host: `discovery.py`, `handler.py` (`ToolkitTaskHandler`: admission, turns, cancel), `translation.py` (`TurnTranslator`), `artifacts.py` (`TurnArtifacts`: artifact versions of a turn), `services.py` (what the server hands a toolkit about a request), `secret_values.py` (masking). |
| `src/aion/server/tasks/context_artifacts.py` | `ContextArtifacts`, the reader of an artifact's versions among the tasks of a context, over the in-memory store or PostgreSQL; `StoreManager.get_context_artifacts()` hands it to discovery. |
| `src/aion/server/tasks/context_runs.py` | `ContextRuns`, the reader of a context's generation and of its unfinished task of one handler, over the in-memory store or PostgreSQL; `StoreManager.get_context_runs()` hands it to discovery. |
| `src/aion/server/agent/execution/extensions/base.py` | `ExtensionTaskHandler`, the contract `ToolkitTaskHandler` implements, and `ExtensionRoute`, the admitted route of a new task. |
| `tests/support/scripted_toolkit/` | The toolkit the SDK's own tests drive the host with; see [Testing](#testing). |

## Discovery

A toolkit registers one object under the `aion.toolkits` entry point group.
`AppFactory` builds the executor with `AionAgentRequestExecutor.create`,
which calls `load_toolkit_handlers(agent_config)` before the agent card is
built. For every entry point:

1. The object is loaded and must satisfy `Toolkit` with a
   `ToolkitDescriptor` as its `descriptor`. Its URI must not belong to
   another extension of the server or to another toolkit.
2. Its extension is registered in `aion_a2a_extension_registry`: URI,
   description, `requires`, and a `MessagesCollector` over the descriptor's
   payload models, so an incoming payload is validated by the registry's
   pipeline before the toolkit sees it. A message part tagged with one of
   their schemas whose data the model refuses is refused with the
   descriptor's `invalid_payload_code` (`invalid_payload` unless the toolkit
   names its own), not retryable, before a task exists. It is active only when the
   descriptor says `enabled_by_default` or the agent lists it in
   `enabled_extensions`; installing a toolkit does not expose it (see
   [Extension exposure](extension-exposure.md)).
3. For a toolkit active for the agent: its protocol version must be
   compatible (`protocol_compatible`), its payload models must declare
   `SCHEMA_URI` and `EVENT_TYPE`, `configure()` must not raise and
   `availability(agent_config)` must return None. Each runs once.
4. A toolkit that passes gets one `ToolkitTaskHandler`, the executor's
   handler for its URI.

A plugin that fails any step is logged as a WARNING naming its entry point
and never stops the server. When its URI is known, the registry records the
reason (`mark_unavailable`): the card leaves the extension out and a request
declaring it is refused with that reason and the code
`extension_unavailable`. A toolkit installed but not enabled is registered
and nothing more; its `configure()` does not run.

## Admission: before a task exists

`AionRequestHandler._setup_active_task` runs, in this order:

1. context admission - the caller may use the message's context;
2. `verify_declared_extensions` - every extension the request declares is
   registered, enabled, available, has its requirements declared, and its
   payload validates;
3. the route - which task the message belongs to, checked by that task's
   handler (`AionRequestHandler._admit_route`);
4. preprocessors (file uploads);
5. a2a-sdk's own setup, which creates the `ActiveTask`.

The handler a message addresses is the first toolkit whose extension it
activates (`AionAgentRequestExecutor.routed_extension`), or the agent's
framework adapter. The message continues a task when it names one, or when
the addressed handler has the caller's newest task in the context and that
task waits for input (`INPUT_REQUIRED`, `AUTH_REQUIRED`;
`AionRequestContextBuilder.interrupted_task`): a toolkit's directive
continues a task of that toolkit, any other message a task of the framework
adapter. A waiting task of another handler keeps waiting, and the message
starts a task of its own. A continued task is named on the message, so
a2a-sdk takes the message as an explicit continuation and nothing decides
again.

A message that continues a task keeps the task's route,
`task.metadata["aion:routedExtension"]`, and is checked by that route alone
(`AionAgentRequestExecutor.admit_continuation`):

- a task routed to an extension this server does not serve - another image
  during a rollout, a toolkit that failed `availability()` - is refused with
  `extension_unavailable`; the task is left as it is and never reaches the
  framework adapter;
- a running task (`SUBMITTED`, `WORKING`) of an `exclusive_per_context`
  toolkit is refused with `context_busy` naming the task itself: a2a-sdk
  would serve the message as the task's next turn, and on another server
  alongside the running one.

Any other message starts a task, and for a toolkit `admit_new_task` runs
`ToolkitTaskHandler.admit`:

- **the caller check** - a toolkit whose descriptor says `invocation_only`,
  on a server with `AION_CLIENT_ID`, serves only a verified caller with an
  invocation token (`CredentialKind.INVOCATION`); anyone else is refused
  with `caller_not_allowed`. Without `AION_CLIENT_ID` nobody is refused.
  The rule follows the setting, not a successful registration with the
  platform, so an unreachable platform does not open the toolkit to all;
- **one task per context** - a toolkit whose descriptor says
  `exclusive_per_context` starts no task in a context that holds an
  unfinished task of it, whoever owns that task; the request is refused with
  `context_busy` naming the task (`ContextRuns.unfinished_task`). The check
  reads the store, so it holds across the servers of the agent. Nothing locks
  the context between the check and the new task's first write; the window
  is that of two requests arriving together. A task left `WORKING` by a
  server that died holds the context until it is cancelled, which any server
  can do, or until it is abandoned - nothing written for longer than
  `AION_STALE_TASK_TIMEOUT`: then the check settles it (see
  [Abandoned tasks](#abandoned-tasks)) and admits the request, unless the
  task's turn runs in this process;
- **`preflight`** - with the `ToolkitRequest` (message id, the message's
  text parts joined by newlines, the payload the registry collected with the
  id and source of its event) and a `PreflightContext` (context id, the
  daemon environment's configuration variables, model service access).

The admitted route is kept on the request's `ServerCallContext.state`
(`EXTENSION_ROUTE_STATE_KEY`); `execute` reads it and records the URI in
`task.metadata["aion:routedExtension"]`, so the next turn and a cancel find
the handler again without deciding anything. Nothing between admission and
`execute` decides the route a second time.

A refusal at any of these steps is raised before a2a-sdk creates or changes
a task, so a refused new task leaves no `tasks`, `task_versions` or
`task_events` row, and a refused continuation leaves its task as it was. A unary send answers with the JSON-RPC error. A streaming send
answers with the error as the stream's only event, as it does for a context
admission refusal: the stream is open by then.

### Errors

A refusal is an a2a-sdk error whose `data` carries `code` and `retryable`,
and for `context_busy` the unfinished task's id under `taskId`.
a2a-sdk sends `data` as the `metadata` of a `google.rpc.ErrorInfo` detail,
whose values are strings, so `retryable` is `"true"` or `"false"`
(`refusal_data`).

| Refusal | a2a-sdk error | `code` | `retryable` |
| --- | --- | --- | --- |
| `preflight` raised `ToolkitRejection` | `InvalidParamsError` | the rejection's | the rejection's |
| `preflight` raised anything else (logged) | `InternalError` | `internal_error` | `"false"` |
| the caller check | `InvalidParamsError` | `caller_not_allowed` | `"false"` |
| an unavailable extension (`verify_declared_extensions`) | `InvalidParamsError` | `extension_unavailable` | `"true"` |
| a payload the toolkit's payload models refuse (`verify_declared_extensions`) | `InvalidParamsError` | the descriptor's `invalid_payload_code`, `invalid_payload` by default | `"false"` |
| a message into a task whose extension this server does not serve | `InvalidParamsError` | `extension_unavailable` | `"true"` |
| a second task of an `exclusive_per_context` toolkit in a context, or a message into its running task | `InvalidParamsError` | `context_busy`, with the unfinished task under `taskId` | `"true"` |

The codes the server reserves and their fixed `retryable` live in
`aion.core.toolkit.codes`. `InvalidParamsError` is the class for every
refusal of a request that could be served under other conditions -
another caller, another deployment state - because it is what a2a-sdk and
this server already answer for a request the agent will not take;
`InternalError` is kept for the toolkit failing. A toolkit's own codes
belong to the toolkit; the server passes them on as they are.

## Turns

`stream` runs the first turn with the admitted request; `resume` runs a later
one with the request read off the executor's runtime context. Both hand the
toolkit a `ToolkitContext`:

| Field | What the server puts there |
| --- | --- |
| `task_id`, `context_id` | The task's. |
| `context_generation` | The time the context's id was reserved (`ContextRuns.generation`), read when the turn starts: the same for every turn while the context lives, new once a deleted context's id is taken again. None for a handler built without a `ContextRuns`. |
| `task_metadata` | The toolkit's object as the task holds it: empty for a new task, `task.metadata[<uri>]` for a resumed one. Whatever a request sent under that key is dropped when the task is created. |
| `daemon_variables` | The request's daemon environment configuration variables. |
| `model_service` | `ModelServiceAccess` - base URL `aion_model_base_url()`, a token minted per call by `aion_jwt_api_key`, and the request's one callback attribution header from `callback_headers()` - or None without platform credentials or without attribution a model call can use. The toolkit decides whether it needs it. |
| `secret_values` | The values the server masks (below). |
| `artifact_finder` | `TurnArtifacts.find` for the turn's caller and context; see [Artifact versions](#artifact-versions). |

A turn is events, then exactly one `ToolkitResult`. A value after the result,
a turn that ends without one, a value that is not a protocol value, a result
whose error has an empty code, or an exception out of the toolkit ends the
turn `FAILED` with `internal_error` and the generic `CRASH_MESSAGE`; the
details go to the log only, and what the turn already delivered stays.

The host asks the toolkit for its next value once it has put the previous
one on the run's queue; a2a-sdk stores it afterwards, on its own schedule.
So a toolkit that waits for the host to take an event knows the event is
queued, not stored: a server that stops in between loses it. A toolkit that
announces an external action (a push) before taking it keeps the action's
outcome recoverable outside the task (the pushed branch), not only in the
announcement.

### Translation

`TurnTranslator` turns each value into the events the executor's pipeline
delivers and stores:

| Protocol | A2A |
| --- | --- |
| `ToolkitMessageEvent` | `TaskStatusUpdateEvent(WORKING)` with the message (role agent) and `metadata{<uri>: object}` |
| the same, `ephemeral` | the same with `aion:ephemeral` and without the object: delivered, never stored |
| the same, `partial` | an `aion:stream-delta` artifact chunk, as the framework adapters stream text; the message's metadata goes to the chunk's artifact under the server's `status` and `status_reason`; the next whole message or the result closes it |
| `ToolkitArtifactEvent` | `TaskArtifactUpdateEvent` with the artifact's id, name, description, parts and metadata plus the server's `version` ([Artifact versions](#artifact-versions)), `append`, `last_chunk`; its `task_metadata` follows as a `WORKING` update with no message |
| `ToolkitStatusEvent` | a `WORKING` update with no message, carrying only the object |
| `ToolkitResult` | its artifacts with `last_chunk`, then the final status in the mapped `TaskState`; its message is the result's, closed for a failure by a text part with the error's message and a data part `{code, message, retryable}` with schema `{uri}#ToolkitError` |
| `TextPart` / `DataPart` / `FilePart` | `Part.text` / `Part.data` with `application/json` / `Part.raw` for bytes and for a path the server reads, `Part.url` for a URL; filename and media type carried |

Every message gets the Event extension's markup: `message.metadata[<event
uri>]` holds `type` (the toolkit's, or `to.aion.toolkit.message` when it
named none), `source` (the toolkit's URI), `id` (the message id),
`sequence` (its place among the messages of the turn, from 1) and `turn`
(the id of the user message that started the turn); a data part's schema
goes to `part.metadata[<event uri>].schema`, and the Event URI is added to
`message.extensions`.

The toolkit's object is the dictionary under its URI in `task.metadata`. A
`task_metadata` patch replaces top-level keys and `None` deletes one; the
translator applies the patch and writes the whole object under the key on
every durable update of a task that has one. a2a-sdk merges event metadata
into the task with `Struct.MergeFrom`, which replaces a key's value rather
than merging into it, so a whole object is the only write that means what it
says. `AionTaskManager` keeps the current `status.message` on a `WORKING`
update with no message that carries metadata, so a status event or an
artifact's details do not blank the task's status line.

### Artifact versions

The same artifact `id` in several tasks of a context is one artifact in
several versions, numbered from 0 in the artifact's `metadata["version"]` -
the key and the decimal-string value the ADK adapter writes too, so a client,
`GetContext` and a lookup read it alike for either kind of agent. A toolkit
never sets it (`Artifact` refuses the key); the server does, for the toolkit's
artifacts only:

- On the first event of an artifact `id` in a task (`TurnTranslator`, from
  `TurnArtifacts.number`), the host reads the context's versions of that `id`
  from the store and writes the next number. Chunks and replacements of the
  artifact in the same task, and the artifact in a later turn of the task, keep
  the number it already has. The count is a query on every call, not a cache,
  so any server of the agent numbers from what the store holds.
- `ToolkitContext.find_artifact(id)` (`TurnArtifacts.find`) returns the newest
  version of `id` in the task's context as a `ContextArtifact`: the protocol's
  `Artifact` converted from A2A (the `version` key moves to the `version`
  field, and keys the server reserves are left out), the writing `task_id`,
  `version` and `updated_at`. Nothing found, or a stored artifact the protocol
  cannot hold, is None.
- Both see only the tasks of the context holder the caller was admitted as
  (`holder_of`; [Admission](#admission-before-a-task-exists)): a private
  context's tasks are its user's, a shared context's are all its participants'.
  A caller that does not hold the context sees none, so it numbers from 0 and
  finds nothing. This is the holder, not the task owner.
- `ContextArtifacts` has an in-memory reader and a PostgreSQL one
  (`TasksRepository.find_artifact_versions`, which filters by `artifact_id`);
  a handler built without one numbers nothing and finds nothing.

Two tasks of one context that run at the same time can compute the same
number; a toolkit that needs one run per context sets
`exclusive_per_context` ([Admission](#admission-before-a-task-exists)).

### Invariants

- One terminal status per turn, written by the server through the run's
  queue: from the result, from a broken turn, or by the executor on a cancel
  the turn did not close itself.
- Every string a toolkit yields - text, data, metadata, the object, names,
  URLs, error messages - is masked: each secret value the server knows is
  replaced with `[REDACTED]`. The values are the deployment's
  (`deployment_secret_values`: variables named for a credential, passwords
  inside URLs), the daemon variables named the same way, and every model
  service token minted for the turn. Values shorter than 8 characters are
  not masked.
- A text part keeps at most `MAX_MESSAGE_TEXT_LENGTH` characters in a
  message and `MAX_ARTIFACT_TEXT_LENGTH` in an artifact; the rest is cut and
  a WARNING logged.
- At most `MAX_DURABLE_MESSAGES_PER_TURN` messages of a turn are stored;
  later ones are delivered ephemerally, with their object write as a separate
  details update, and a WARNING is logged.
- A file in bytes or at a path travels as `Part.raw`, which
  `A2AFileTransformer` stores and replaces with a link; a file that cannot be
  stored fails the task, as for any agent.
- The final status closes an open stream delta and gives every artifact the
  turn left unfinished an empty `last_chunk` update.

### Cancel

`CancelTask` always answers with a task that has an outcome, as in a2a-sdk,
and the task has one writer of its terminal state: the run's queue.

- **The turn runs on this server.** a2a-sdk calls the executor's `cancel`
  under its lock on the task; `ToolkitTaskHandler.cancel` calls
  `Toolkit.cancel(task_id, "cancel", within=CANCEL_GRACE_SECONDS)` and waits
  that long (15 s) for the turn to end. In that time the toolkit may still yield artifacts and
  its result - `canceled`, or `completed` for work it had already delivered,
  which `CancelTask` then answers with. They reach the task, and the stream
  open on it, as any turn's would, and nothing more is written. Past the
  grace period, or for a turn that stopped to wait for input, the executor
  writes `CANCELED` into the same queue, so the open stream still closes
  with it; a2a-sdk then cancels the turn, and what the toolkit does after
  that goes to its log only. The wait is short because it holds the lock: a
  `SubscribeToTask` that arrives meanwhile is answered without the lock
  (`AionRequestHandler.on_subscribe_to_task`) - the stored task at once, the
  task with its outcome once the cancel is over.
- **The turn runs on another server, or nowhere.** a2a-sdk writes
  `CANCELED` over the stored version and answers at once;
  `AionRequestHandler._write_cancel` sends its push notification. The server
  running the turn learns of it at its next durable write: the version
  conflict makes a2a-sdk cancel its producer, and the turn, torn down before
  its outcome, calls `Toolkit.cancel(task_id, "cancel", within=None)`: the
  server waits for no result. Nothing more is written into the task. Until that write the turn goes on: the delay is up
  to the toolkit's next durable event.
- **No extension is activated.** The executor's `cancel` runs with the
  caller's attribution and none of the request's extensions: a daemon named
  in `A2A-Extensions` has no payload on a cancel and must not fail it.

### Stopping

A turn is asked to stop at most once (`Toolkit.cancel`); a later reason does
not ask again. `within` tells the toolkit how many seconds from the call the
server waits for the turn's result: `CANCEL_GRACE_SECONDS` for a caller's
cancel, the toolkits' window of the shutdown budget at a shutdown, and None
when it waits for none - a turn torn down, a run without a consumer, a
cancel of a task whose turn does not run here.

- **Server shutdown.** `AION_SHUTDOWN_TIMEOUT` (seconds, 25 by default) is
  the agent's shutdown budget: `aion serve` gives every agent that long, all
  of them at the same time, before it kills them, and the agent divides it
  among its steps (`aion.server.shutdown.ShutdownBudget`). The agent's
  uvicorn server (`_AgentServer` in `aion.server.server`) stops listening and
  closes idle connections, then runs `AppFactory.stop_toolkit_turns`: every
  handler asks each turn running in the process to stop with `SHUTDOWN` and
  the toolkits' window as `within`, all at once, and the turns are awaited
  together for that window (`ToolkitTaskHandler.shutdown`). A toolkit stops
  its work, saves what it can within the window and closes the turn with a
  `failed` result coded `server_shutdown` (`retryable`): one writer, through
  the run's queue, so the stream open on the task, a push and the store all
  get it. uvicorn then waits for the connections still open
  (`timeout_graceful_shutdown`) and cancels them, and the drain of the
  active tasks settles a turn that did not stop as `FAILED` with
  `aion:settledReason=server_shutdown`; what such a turn did not save is
  lost with the process. Of the default 25 s the toolkits get 15, uvicorn 5,
  the settlement 2 and the rest of the cleanup 3. A smaller budget shrinks
  the last three - uvicorn at most 20 % of it - and the toolkits get the
  rest, at least 1 s; below 10 s the agent logs a WARNING at startup. A
  larger one goes to the toolkits. A deployment sets the variable a few
  seconds below its platform's stop timeout (Kubernetes
  `terminationGracePeriodSeconds`, `docker stop -t`), so the server finishes
  its shutdown before the platform kills it.
- **A run without a consumer.** When the consumer of a run's events fails -
  a store write that is not a version conflict, the store unreachable -
  a2a-sdk closes the run's queue and drops whatever the run writes after it,
  without stopping the run. Before each event the turn checks
  `EventQueue.is_closed()`; once the queue is closed it asks the toolkit to
  stop (`CANCEL`), logs it, and ends without writing. The task keeps what
  a2a-sdk wrote for it: `FAILED`, or the active state, which a cancel ends.

### Abandoned tasks

A task in `SUBMITTED` or `WORKING` with no durable write for longer than
`AION_STALE_TASK_TIMEOUT` (seconds, 3600 by default, `0` off) is taken for
abandoned: its server died or hung. `aion.server.tasks.abandoned` settles it
`FAILED` with `aion:settledReason=task_abandoned`; a task routed to a toolkit
also gets, as its status message, the error a failed turn closes with (`code`
`task_abandoned`, `retryable` true, schema `{uri}#ToolkitError`), so its
client reads both alike. There is no lease and no heartbeat, only the time of
the task's last write, read on the database's clock. The write is the one
a2a-sdk's remote cancel makes - at the version read, with its journal event,
then a push notification - so a task written in the meantime keeps its state,
and a turn still alive on some server meets the version conflict at its next
write and is torn down: `Toolkit.cancel(task_id, "cancel", within=None)`.

Three places settle, on every server, never a task whose turn runs in their
own process and never one waiting for input:

- the one task per context check of an `exclusive_per_context` toolkit, for
  the task that holds the context; the request is then admitted;
- a message that names an abandoned task (`AionRequestHandler._settle_if_abandoned`);
  the message is then refused as one into a task that has an outcome;
- a background sweep over the agent's tasks every 60 s, or every half of a
  smaller timeout (`AionRequestHandler.sweep_abandoned_tasks`, started by the
  app's lifespan). A stale task whose turn runs in this process is logged
  as a WARNING and left: it is alive here, however silent.

A turn that runs longer than the timeout without yielding a durable event -
a status, a message, an artifact - is taken for abandoned by the other
servers of the agent; a toolkit writes a status now and then during long
work.

## Testing

`tests/support/scripted_toolkit` is a toolkit built on the protocol models
and nothing else. It is installed twice: under its own URI, with
`INVALID_SCRIPT_CODE` as its `invalid_payload_code`, and under
`EXCLUSIVE_URI` as an `exclusive_per_context` toolkit with a script event of
its own and the default code. A turn yields what its script says: the `Script` it was
built with in a unit test, or the `ScriptPayload` a request carries - an
event of type `SCRIPT_EVENT_TYPE` with a data part of schema `SCRIPT_SCHEMA`
whose `outputs` are protocol values in JSON. A script can also refuse or
crash in `preflight`, crash after some outputs, look an artifact up in its
context, or wait for `cancel()` - writing on a tick meanwhile, and then
ending `canceled`, `completed`, `failed` with `server_shutdown` for a
shutdown (with the `within` it was given as `stopped_within` in its object),
or not at all. The toolkit records what the server handed
it, and a script with `report` has a turn say it in a message: how the turn
began, its task, the object the task held and every `cancel()` the toolkit was
asked for, with its reason and `within` - which is how a scenario, whose
server is another process, reads the record. `contract.py` has its URI and schemas without importing the SDK,
for the scenarios; `tests/scenarios/toolkits.py` has the deployment and the
helpers that send a script and read a report.

`site/` holds the distribution metadata of an `aion.toolkits` entry point
naming the toolkit. With that directory on `sys.path` - `PYTHONPATH` for a
server process - discovery finds the toolkit as an installed one, with
nothing installed.

| Suite | Where | What |
| --- | --- | --- |
| unit | `tests/unit/server/agent/toolkit/` | discovery, a refused payload's code, every translation row, the object's patches, masking, limits, lifecycle violations, admission, one task per context, the context's generation, resume, cancel, a run without a consumer, shutdown, artifact versions and `find_artifact` |
| unit | `tests/unit/server/test_server.py`, `tests/unit/server/test_shutdown.py`, `tests/unit/server/utils/test_process_manager.py`, `tests/unit/cli/test_serve_handler.py` | the order of a shutdown, `AION_SHUTDOWN_TIMEOUT` and its division among the steps, agents stopped side by side within it |
| unit | `tests/unit/server/agent/test_request_executor.py`, `tests/unit/server/core/test_resubscribe_contract.py`, `tests/unit/server/core/test_remote_cancel_push.py` | one writer of a cancel's outcome, no extension activated on a cancel, a subscription not held by a cancel, the push of a remote cancel |
| unit | `tests/unit/server/core/test_preprocessor_ordering.py`, `tests/unit/server/agent/test_request_executor.py` | the route of a continued task: its handler's check, `extension_unavailable` without the handler, the waiting task of the addressed handler named on the message |
| unit | `tests/unit/server/tasks/test_context_artifacts.py` | the in-memory reader of an artifact's versions: order, holder scoping, shared contexts |
| unit | `tests/unit/server/tasks/test_task_manager.py` | the status line a details update keeps |
| unit | `tests/unit/server/tasks/test_abandoned.py`, `tests/unit/server/agent/toolkit/test_handler.py`, `tests/unit/server/core/test_request_handlers.py` | what is abandoned and what its settlement writes over the in-memory store, a task written meanwhile left, the sweep and a task running here, `AION_STALE_TASK_TIMEOUT`; an abandoned task not holding an exclusive toolkit's context; a message into an abandoned task |
| integration | `tests/integration/server/tasks/test_toolkit_admission_postgres.py` | a refused request leaves no row in `tasks`, `task_versions` or `task_events`, and the error carries `code` and `retryable`; one task per context across two servers of the agent; a message into a toolkit's task on a server without the toolkit refused, the task unchanged; an abandoned task settled by the other server's admission, a message into it refused, the server still running it stopping at its next write |
| integration | `tests/integration/server/tasks/test_abandoned_tasks_postgres.py` | the settlement on PostgreSQL: a versioned write with its journal entry, a toolkit's error, tasks written lately, waiting for input or finished left, another agent's task not swept, the version conflict |
| integration | `tests/integration/server/tasks/test_context_runs_parity.py` | the generation, the unfinished task of a handler and the caller's newest task of a handler - on the in-memory store and on PostgreSQL |
| integration | `tests/integration/server/tasks/test_context_artifacts_parity.py` | versions 0, 1, 2 across tasks, the latest found, another holder seeing nothing, shared contexts - on the in-memory store and on PostgreSQL |
| scenarios | `tests/scenarios/core/test_toolkits.py` (`TAGS=toolkits`) | a turn through a real `aion serve` on the `toolkit` deployment, numbered artifact versions with `find_artifact`, a refusal with no task, a payload the toolkit's model refuses answered with the descriptor's code and no task, a cancel that reaches the toolkit once and ends the task `CANCELED`, `context_busy` until the blocking task ends, a directive beside the agent's waiting question, a toolkit continuing its own waiting task, a cancel past the grace period with a subscription not held by it, a cancel answered `COMPLETED` for delivered work |
| scenarios | `tests/scenarios/persistence/test_toolkits.py` (`make tests-scenarios-persistence`) | on a real database: the toolkit's object handed back to a resumed turn after a restart, artifact version numbering continuing across it, a shutdown closing a turn with the toolkit's `server_shutdown` on its open stream, a turn that does not stop in time settled, an open stream not holding the shutdown, a small `AION_SHUTDOWN_TIMEOUT` giving the toolkit a shorter `within` |
| scenarios | `tests/scenarios/distributed/test_toolkits.py` (`make tests-scenarios-distributed`) | two servers with the toolkits and one without on one database: `context_busy` on the other server, `extension_unavailable` and an unchanged task on the server without the toolkit, a cancel on the other server reaching the toolkit at the owner's next write |
| scenarios | `tests/scenarios/distributed/test_abandoned_tasks.py` (`make tests-scenarios-distributed`) | two servers with a small `AION_STALE_TASK_TIMEOUT`: a hung turn settled by the other server's sweep and a waiting task left, an abandoned task freeing its context and refusing a message, a server killed mid-turn whose toolkit and framework tasks the survivor settles |
| scenarios | `tests/scenarios/core/test_daemon.py` (`TAGS=daemon`) | `GetTask` and `CancelTask` with the daemon named in `A2A-Extensions` |

A real toolkit is checked against the SDK by its own CI; see
[Continuous integration](ci.md#toolkits).
