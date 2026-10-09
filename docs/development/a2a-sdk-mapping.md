# a2a-sdk to Aion mapping

The agent server is built on a2a-sdk's server package (`a2a.server`, pinned in
`pyproject.toml`). This page maps every a2a-sdk server entry point the SDK
touches to what Aion does with it, so a change on either side can be checked
against the other. It describes the code as it is; update it in the same
change as the code, and on every a2a-sdk bump (see `AGENTS.md`).

Status of an entry point:

| Status | Meaning |
|---|---|
| as is | Used unchanged. |
| extended | Subclassed; the override delegates to a2a-sdk and adds around it. |
| replaced | Subclassed or reimplemented; Aion's body stands in for a2a-sdk's. |
| not used | Available in a2a-sdk and deliberately left out. |
| added | Aion's own, with no a2a-sdk counterpart. |

`tests/unit/server/test_sdk_override_parity.py` holds the same list of
overrides as [Overridden methods](#overridden-methods), and
`tests/unit/server/test_a2a_sdk_mapping.py` checks that the two agree.

## JSON-RPC methods

Every method the JSON-RPC endpoint answers. The A2A 1.0 methods are
`JsonRpcDispatcher.METHOD_TO_MODEL`, the A2A 0.3 methods
`JSONRPC03Adapter.METHOD_TO_MODEL`, which serves each one through the 1.0
handler; `tests/unit/server/core/test_a2a_surface.py` pins both lists and the
routes, and `tests/unit/server/test_a2a_sdk_mapping.py` checks this table
against them.

| Method | Protocol | Status | Aion | What differs |
|---|---|---|---|---|
| `SendMessage` | 1.0 | extended | `AionRequestHandler.on_message_send` | Answers with a `Task`, never a bare `Message`; context admission, extension verification and file preprocessing run first. |
| `SendStreamingMessage` | 1.0 | extended | `AionRequestHandler.on_message_send_stream`, `AionJsonRpcDispatcher._process_streaming_request` | Extensions verified before the SSE headers; the stream opens and closes with the stored `Task`. |
| `GetTask` | 1.0 | extended | `AionRequestHandler.on_get_task` | The caller's own tasks only. |
| `ListTasks` | 1.0 | extended | `AionRequestHandler.on_list_tasks` | The caller's own tasks only; opaque page cursors. |
| `CancelTask` | 1.0 | extended | `AionRequestHandler.on_cancel_task` | The caller's own tasks only. |
| `SubscribeToTask` | 1.0 | extended | `AionRequestHandler.on_subscribe_to_task` | The caller's own tasks only; the stream closes with the stored `Task`. |
| `CreateTaskPushNotificationConfig` | 1.0 | extended | `AionRequestHandler.on_create_task_push_notification_config` | The task's initiator only; on a hosted server the URL must be public. |
| `GetTaskPushNotificationConfig` | 1.0 | extended | `AionRequestHandler.on_get_task_push_notification_config` | The task's initiator only. |
| `ListTaskPushNotificationConfigs` | 1.0 | extended | `AionRequestHandler.on_list_task_push_notification_configs` | The task's initiator only. |
| `DeleteTaskPushNotificationConfig` | 1.0 | extended | `AionRequestHandler.on_delete_task_push_notification_config` | The task's initiator only. |
| `GetExtendedAgentCard` | 1.0 | as is | - | The card declares no extended card: `UnsupportedOperationError` (`-32004`). |
| `message/send` | 0.3 | as is | through `SendMessage` | |
| `message/stream` | 0.3 | extended | `AionJSONRPC03Adapter._process_streaming_request` | LF event delimiters; extensions verified before the stream opens. |
| `tasks/get` | 0.3 | as is | through `GetTask` | |
| `tasks/cancel` | 0.3 | as is | through `CancelTask` | |
| `tasks/resubscribe` | 0.3 | extended | `AionJSONRPC03Adapter._process_streaming_request` | LF event delimiters. |
| `tasks/pushNotificationConfig/set` | 0.3 | as is | through `CreateTaskPushNotificationConfig` | |
| `tasks/pushNotificationConfig/get` | 0.3 | as is | through `GetTaskPushNotificationConfig` | |
| `tasks/pushNotificationConfig/list` | 0.3 | as is | through `ListTaskPushNotificationConfigs` | |
| `tasks/pushNotificationConfig/delete` | 0.3 | as is | through `DeleteTaskPushNotificationConfig` | |
| `agent/getAuthenticatedExtendedCard` | 0.3 | as is | through `GetExtendedAgentCard` | |
| `GetContexts` | Aion | added | `AionJsonRpcDispatcher._handle_method_extension` | The Context extension; errors in a2a-sdk's format, `A2A-Version` checked, a call without it served. |
| `GetContext` | Aion | added | `AionJsonRpcDispatcher._handle_method_extension` | As `GetContexts`. |
| `DeleteContext` | Aion | added | `AionJsonRpcDispatcher._handle_method_extension` | As `GetContexts`. |

## Deployment modes

| a2a-sdk mode | Selected by | Aion |
|---|---|---|
| Single process: plain `TaskStore`, no `event_stream` | no `POSTGRES_URL` | `InMemoryTaskStore`; a2a-sdk wraps it in `LegacyTaskStoreAdapter`. One server only. |
| Cluster: `VersionedTaskStore` with a `TaskEventStream` | `POSTGRES_URL` set | `PostgresVersionedTaskStore` over `PostgresTaskStore`, with a2a-sdk's `DatabaseTaskEventStream`. Any number of servers of one agent. |

`StoreManager` picks the pair; `AppFactory` hands it to `AionRequestHandler`.

## Cluster mode

| a2a-sdk | Status | Aion | Why |
|---|---|---|---|
| `VersionedTaskStore` contract | as is | `PostgresVersionedTaskStore` implements it | The contract is a2a-sdk's: version per task, compare-and-swap, `CANCELED` overwrites a non-terminal task, event journaled in the same transaction. |
| `VersionedDatabaseTaskStore` | not used | `PostgresVersionedTaskStore` | It stores a task as one row with JSON columns; Aion's `tasks` keeps history and artifacts in `task_messages` and `task_artifacts`, with `agent_id` and `owner_scope`, and contexts reference it. The version and journal tables are a2a-sdk's own models. |
| `TaskVersionModel`, `TaskEventModel` (`task_versions`, `task_events`) | as is | written by `PostgresVersionedTaskStore`, created by migration `008` | Same columns as a2a-sdk's revision `b5e3d1c8a2f7`, in Aion's schema and migration chain. |
| `a2a/migrations` | not used | `aion.db.postgres.migrations` | All of the SDK's tables come from one migration chain; see [Database migrations](#database-migrations). |
| `DatabaseTaskEventStream` | as is | `StoreManager`, `create_table=False` | Polls the journal every 0.5 s. |
| `LegacyTaskStoreAdapter` | as is | in-memory store | a2a-sdk applies it to any plain store. |
| `TaskVersion`, `StoredTask`, `ConcurrentTaskModificationError` | as is | stores, task manager, registry | `ConcurrentTaskModificationError` is also the refusal of a write into a context being deleted, and of a write that would move a terminal task to another state. |

What a2a-sdk's cluster mode does not provide, and Aion does not add either:

| Missing upstream | Consequence here |
|---|---|
| Exclusive execution of a task | Two servers can execute one task at once; the version decides which writes land. |
| `AgentExecutor.cancel` on the executing server for a remote cancel | The executing server stops at its next write. For an evolution run `EvolutionHandler.cancel` does not run, so no `evolution-rescue-bundle` artifact and no closing cancel message; the stop closes the worker's stream, and whatever the toolkit does on that close is all that happens. |
| Detection of a dead server | Its tasks keep the state they had until someone cancels them. |
| Journal cleanup | `task_events` grows with every stored event; deleting a task keeps its events. |
| A wait hook in `DatabaseTaskEventStream` | A remote subscriber sees events up to one poll interval late. |

What Aion adds on top of cluster mode:

| Addition | Where | Why |
|---|---|---|
| A stream from the journal closes with the stored `Task` | `AionRequestHandler.on_subscribe_to_task`, `TerminalTaskProjection` | The streaming contract: every stream opens and closes with a `Task`. |
| A terminal task is final | `AionTaskManager._save_task` | The producer saves `Task` and `Message` events itself (`AionEventPipeline._save_silently`); after the consumer rereads a task another server cancelled, that save would otherwise pass the version check. A write in another state is refused; a write in the same state - a late reply or artifact - writes nothing. |
| The writes of one server's task manager take turns | `AionTaskManager._save_task` | The consumer and the producer's own saves share one manager; without turns, one would be refused as stale because of the other. |
| Version row locked before task row everywhere | `PostgresVersionedTaskStore.save`, `PostgresTaskStore.delete`, `PostgresContextCatalog.finish_deletion` | A write and a delete of one task cannot deadlock. |
| Shutdown settles interrupted tasks as `FAILED` | `AionActiveTaskRegistry.aclose` | The execution is gone; the write is versioned, so a task another server moved on keeps its state. |
| Inline file content stripped from journaled events | `PostgresVersionedTaskStore._persistable_event` | The journal is persistence too. |
| Version rows removed with a deleted context's tasks | `PostgresContextCatalog.finish_deletion` | A late write of a removed task is then stale, and as a new task admission refuses it. |
| Live-only events are not journaled | `AionTaskManager._check_process_skip_event` | Response and thinking deltas and ephemeral progress are never stored, so only a subscriber on the executing server receives them. |

## Overridden methods

| Aion | a2a-sdk | Status | What differs and why | Tested by |
|---|---|---|---|---|
| `AionRequestHandler._setup_active_task` | `DefaultRequestHandlerV2._setup_active_task` | extended | Context admission, extension verification and preprocessing (file uploads) run first, so a refused message leaves nothing behind. | `test_context_admission_a2a_jsonrpc.py`, `test_preprocessor_ordering.py` |
| `AionRequestHandler.on_get_task` | `DefaultRequestHandlerV2.on_get_task` | extended | A caller without individual access gets `TaskNotFoundError`. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionRequestHandler.on_list_tasks` | `DefaultRequestHandlerV2.on_list_tasks` | extended | A caller without individual access lists nothing. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionRequestHandler.on_cancel_task` | `DefaultRequestHandlerV2.on_cancel_task` | extended | Individual access check; local and remote cancellation are a2a-sdk's. | `test_cluster_mode_postgres.py`, `test_owner_isolation_postgres.py` |
| `AionRequestHandler.on_message_send` | `DefaultRequestHandlerV2.on_message_send` | extended | A bare `Message` result is answered with the stored `Task`. | `test_unary_task_contract.py` |
| `AionRequestHandler.on_message_send_stream` | `DefaultRequestHandlerV2.on_message_send_stream` | extended | `TerminalTaskProjection`: the stream opens and closes with the stored `Task`. | `test_stream_contract_parity.py`, `test_terminal_task_projection.py` |
| `AionRequestHandler.on_subscribe_to_task` | `DefaultRequestHandlerV2.on_subscribe_to_task` | extended | Individual access check; the terminal-task refusal is checked before `TerminalTaskProjection`, which closes the local and the remote stream alike with the stored `Task`. | `test_resubscribe_contract.py`, `test_cluster_mode_postgres.py` |
| `AionRequestHandler.on_create_task_push_notification_config` | `DefaultRequestHandlerV2.on_create_task_push_notification_config` | extended | Individual access check. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionRequestHandler.on_get_task_push_notification_config` | `DefaultRequestHandlerV2.on_get_task_push_notification_config` | extended | Individual access check. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionRequestHandler.on_list_task_push_notification_configs` | `DefaultRequestHandlerV2.on_list_task_push_notification_configs` | extended | Individual access check. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionRequestHandler.on_delete_task_push_notification_config` | `DefaultRequestHandlerV2.on_delete_task_push_notification_config` | extended | Individual access check. | `test_token_isolation_a2a_jsonrpc.py` |
| `AionActiveTaskRegistry.get` | `ActiveTaskRegistry.get` | replaced | A finished `ActiveTask` is reported as absent, so the handler serves the task from the store or the journal. | `test_cluster_mode_postgres.py` |
| `AionActiveTaskRegistry.get_or_create` | `ActiveTaskRegistry.get_or_create` | replaced | Builds `AionTaskManager` and a per-task `TerminalTaskPushSender`, populates the execution scope, and replaces a finished `ActiveTask` instead of returning it. Keeps a2a-sdk's owner check on a cache hit and passes `event_stream` through. | `test_sdk_override_parity.py`, `test_task_interrupt_teardown.py` |
| `AionActiveTaskRegistry._on_active_task_cleanup` | `ActiveTaskRegistry._on_active_task_cleanup` | replaced | Removes only the incarnation that finished, not a newer one under the same id. | `test_task_interrupt_teardown.py` |
| `AionActiveTaskRegistry._remove_task` | `ActiveTaskRegistry._remove_task` | extended | Drops the task manager with the entry. | `test_active_task_registry_shutdown.py` |
| `AionActiveTaskRegistry.aclose` | `ActiveTaskRegistry.aclose` | extended | Settles tasks the drain interrupted as `FAILED` (`server_shutdown`). | `test_active_task_registry_shutdown.py`, `persistence/test_restart.py` |
| `AionTaskManager.invalidate` | `TaskManager.invalidate` | extended | The next store read becomes the baseline state for transitions. | `test_task_manager.py` |
| `AionTaskManager.get_task` | `TaskManager.get_task` | extended | Records the state the store read brought back. | `test_task_manager.py` |
| `AionTaskManager.ensure_task_id` | `TaskManager.ensure_task_id` | extended | Records the state the store read brought back. | `test_task_manager.py` |
| `AionTaskManager._save_task` | `TaskManager._save_task` | extended | Serializes the manager's writes. Refuses a write that changes a terminal state and skips a write that keeps it. Fills a missing status timestamp before storage and delivery. Schedules the `ActiveTask` teardown on a move into `INPUT_REQUIRED` or `AUTH_REQUIRED`. | `test_task_manager.py`, `test_task_manager_postgres.py` |
| `AionTaskManager.process` | `TaskManager.process` | extended | Fills missing status timestamps before storage and delivery, even for live-only status events. Excludes live-only events from storage. Stores a standalone `Message` as a status update and keeps the last reply on the final status. | `test_task_manager.py`, `test_task_manager_ephemeral.py`, `test_events.py`, `test_push_notifications.py` |
| `AionJsonRpcDispatcher.handle_requests` | `JsonRpcDispatcher.handle_requests` | extended | Routes Aion's method extensions; every standard method goes to a2a-sdk. A method extension answers errors in a2a-sdk's format (`ErrorInfo` details) and checks `A2A-Version` by its rule, except that a call without the header is served. | `test_method_extension_bindings.py`, `test_jsonrpc_dispatcher.py`, `test_error_format.py` |
| `AionJsonRpcDispatcher._process_streaming_request` | `JsonRpcDispatcher._process_streaming_request` | extended | Verifies a send's extension activation before the SSE headers go out. | `test_jsonrpc_dispatcher.py` |
| `AionJsonRpcDispatcher._create_response` | `JsonRpcDispatcher._create_response` | extended | LF event delimiters in SSE, safe through tunnels. | `test_jsonrpc_dispatcher.py` |
| `AionJSONRPC03Adapter._process_streaming_request` | `JSONRPC03Adapter._process_streaming_request` | extended | The A2A 0.3 streams follow the 1.0 binding's rules: LF event delimiters, and a `message/stream` whose extensions fail verification refused with `-32602` before the stream opens. | `test_v03_streams.py` |
| `AionRequestContextBuilder.build` | `RequestContextBuilder.build` | replaced | A message with a `contextId` and no `taskId` continues the caller's interrupted task in that context. | `test_request_context_builder.py` |
| `AionAgentRequestExecutor.execute` | `AgentExecutor.execute` | replaced | Runs the agent's framework adapter or a routed extension handler through `AionEventPipeline`. | `test_request_executor.py` |
| `AionAgentRequestExecutor.cancel` | `AgentExecutor.cancel` | replaced | Delegates to the framework adapter or extension handler, then writes `CANCELED`; runs only for a task this server holds. | `test_request_executor.py` |
| `TerminalTaskPushSender.send_notification` | `PushNotificationSender.send_notification` | replaced | Sends the stored `Task` in place of a non-active status update. | `test_terminal_push_sender.py` |
| `AionPushNotificationSender.send_notification` | `BasePushNotificationSender.send_notification` | replaced | Same delivery; reports per-webhook outcomes instead of one summary line. | `test_push_sender.py` |
| `AionPushNotificationSender._dispatch_notification` | `BasePushNotificationSender._dispatch_notification` | replaced | Same delivery; credentials without a scheme go as `Bearer`; logs status and timing. | `test_push_sender.py` |
| `InMemoryTaskStore.save` | `TaskStore.save` | replaced | Owner fixed by the first write; refuses a write into a deleted context. | `test_store_owner_parity.py` |
| `InMemoryTaskStore.get` | `TaskStore.get` | replaced | Scoped to the caller's owner; `None` is unscoped. | `test_store_owner_parity.py` |
| `InMemoryTaskStore.delete` | `TaskStore.delete` | replaced | Scoped to the caller's owner. | `test_store_owner_parity.py` |
| `InMemoryTaskStore.list` | `TaskStore.list` | replaced | Scoped to the caller's owner, opaque page cursors. | `test_store_owner_parity.py` |
| `PostgresTaskStore.save` | `TaskStore.save` | replaced | Aion's schema: head row, message and artifact tables, owner fixed by the first write, admission check for a new task. | `test_postgres_task_store.py`, `test_store_owner_parity.py` |
| `PostgresTaskStore.get` | `TaskStore.get` | replaced | Hydrates history and artifacts from one snapshot. | `test_postgres_task_store.py` |
| `PostgresTaskStore.delete` | `TaskStore.delete` | replaced | Removes the version row with the task, as a2a-sdk's versioned store does, locking it first. | `test_versioned_store_postgres.py` |
| `PostgresTaskStore.list` | `TaskStore.list` | replaced | Keyset pagination with filter-bound page tokens. | `test_postgres_task_store.py` |
| `PostgresVersionedTaskStore.save` | `VersionedTaskStore.save` | replaced | a2a-sdk's contract over Aion's schema; strips inline file content from the journaled event. | `test_versioned_store_postgres.py` |
| `PostgresVersionedTaskStore.get` | `VersionedTaskStore.get` | replaced | Task and version from one snapshot; a task without a version row reads as `MISSING`. | `test_versioned_store_postgres.py` |
| `PostgresVersionedTaskStore.delete` | `VersionedTaskStore.delete` | replaced | Delegates to `PostgresTaskStore.delete`. | `test_versioned_store_postgres.py` |
| `PostgresVersionedTaskStore.list` | `VersionedTaskStore.list` | replaced | Delegates to `PostgresTaskStore.list`. | `test_store_owner_parity.py` |

## Database migrations

a2a-sdk ships Alembic migrations for its tables in `a2a/migrations`, run by its
`a2a-db` command. The SDK never runs them. Every table in the `aion` schema,
the ones built on a2a-sdk's models included, comes from the SDK's own chain in
`aion.db.postgres.migrations`, so one `alembic_version` says where a database
stands.

Every a2a-sdk revision is listed below, one row per table it touches, with the
SDK revision that makes the same change or `not applied` and the reason.
`tests/unit/db/test_a2a_migration_registry.py` fails when the installed
a2a-sdk has a revision this table does not list, or lists one it does not
have. `tests/integration/db/test_a2a_tables_match_models.py` compares the
tables after the SDK migrations with a2a-sdk's models: columns, types,
nullability, keys and indexes.

| a2a-sdk revision | Change | Aion revision | Why |
|---|---|---|---|
| `6419d2d130f6` | `tasks`: `owner`, `last_updated`, index on both | not applied | Aion's `tasks` is its own table, scoped by `owner_scope`. |
| `6419d2d130f6` | `push_notification_configs`: `owner`, index on `owner` | `009` | Created with both; added to a table a2a-sdk's store created without them. |
| `38ce57e08137` | `tasks`: `protocol_version` | not applied | Aion's `tasks` is its own table. |
| `38ce57e08137` | `push_notification_configs`: `protocol_version` | `009` | Created with it; added to a table that lacks it. |
| `b5e3d1c8a2f7` | `task_versions`, `task_events`, index on `task_events.task_id` | `008` | Same columns. |

When an a2a-sdk upgrade brings a revision, write an SDK revision that makes
the same change to the tables Aion keeps on a2a-sdk's models, or mark the part
`not applied` with the reason, and add its rows here in the same change.

## Used as is

| a2a-sdk | Where |
|---|---|
| `ActiveTask` | built by `AionActiveTaskRegistry` |
| `TaskUpdater`, `EventQueue` | `AionAgentRequestExecutor`, `AionEventPipeline` |
| `add_a2a_routes_to_fastapi`, `create_agent_card_routes` | `AppFactory._build_app` |
| A2A 0.3 compatibility (`enable_v0_3_compat=True`) | `AionJsonRpcDispatcher`, which installs `AionJSONRPC03Adapter` as the adapter |
| `InMemoryPushNotificationConfigStore` | `PushNotificationFactory` |
| `DatabasePushNotificationConfigStore` | `AionDatabasePushNotificationConfigStore`, built by `PushNotificationFactory`: a `MultiFernet` of every `ENCRYPTION_KEY` in place of the single key, and `create_table=False`, since the table comes from migration `009` |
| `validate_push_notification_url` as `push_url_validator` of `DefaultRequestHandlerV2` and `BasePushNotificationSender` | `AppFactory`, on a server the platform hosts only (`push_url_validator()`) |
| `resolve_user_scope`, `OwnerResolver` | default owner resolver of the stores and the agent |

## Not used

| a2a-sdk | Why |
|---|---|
| `validate_input_modes` of `DefaultRequestHandlerV2` | It compares a part's media type with the card's modes literally, so the `image/*` an `image` mode stands for would refuse every `image/png`; and it runs after the file preprocessors. A part the agent cannot take is refused by the file handling, which says why. |
| `DefaultRequestHandler` (v1) and `QueueManager` | The server is built on `DefaultRequestHandlerV2`. |
| `a2a.server.tasks.InMemoryTaskStore`, `DatabaseTaskStore` | Aion's stores keep owner scope, context admission and Aion's schema. |
| `VersionedDatabaseTaskStore` | See [Cluster mode](#cluster-mode). |
| gRPC and REST transports | The A2A methods are served over JSON-RPC; the Context extension adds its own HTTP+JSON routes. |
