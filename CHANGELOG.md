# Changelog

## Unreleased


### ⚠ BREAKING CHANGES

* **deps:** require a2a-sdk >=1.2.2,<1.3.0
* **server:** require a verified caller on every endpoint except the agent card, health and configuration schema; refuse others with 401 (JSON-RPC error -32051)
* **server:** replace the per-method GetContext/GetContexts extensions with the unified Context extension
* **server:** refuse a message to a task with an outcome with UnsupportedOperationError (-32004) instead of InvalidParamsError (-32602)
* **server:** rename AuthenticatedPushNotificationSender to AionPushNotificationSender (aion.server.tasks.push_sender)
* **server:** run a2a-sdk's cluster mode on PostgreSQL in place of task claims: any server executes, follows and cancels any task, and TaskOwnershipBusy (-32050) is gone
* **server:** answer SubscribeToTask on a task with an outcome with UnsupportedOperationError (-32004) instead of the stored Task
* **server:** cancel a task running on another server by writing CANCELED over its stored version; AgentExecutor.cancel no longer runs on the executing server
* **server:** leave the tasks of a server that died in the state they had instead of settling them as FAILED (lease_expired); remove TASK_OWNERSHIP_REAPER and TASK_OWNERSHIP_LEASE_TTL_SECONDS
* **server:** drop the lease_expired, cancel_requested and cancel_timeout settlement reasons
* **db:** migration 008 adds task_versions and task_events and drops task_claims; stop every server of an agent before migrating
* **adk:** drop the file_uploader parameter of ADKAdapter and ADKExecutor: the agent server stores ADK artifacts with every other outbound file, and an ADKExecutor used on its own leaves their content inline
* **server:** stop passing file_upload_manager to AgentPluginProtocol.initialize: the agent server stores every outbound file itself
* **server:** A2AFileTransformer.transform_event returns an EventTransform (the event and a report of the files it could not store) instead of the event alone


### Features

* **server:** verify Aion invocation and session tokens and serve each task as its caller
* **server:** implement the Context extension (GetContexts, GetContext, DeleteContext) over JSON-RPC and HTTP+JSON
* **server:** accept deployment-initiated callbacks and scope callback attribution per request
* **server:** expose the typed scheduled invocation context of the cron extension
* **server:** welcome new threads on request through the welcome-message extension
* **server:** propagate signed usage attribution through Python clients
* **server:** answer CreateTaskPushNotificationConfig with the stored config, its empty id set to the task id
* **server:** serve the agent card with a weak ETag and answer a matching If-None-Match with 304
* **server:** ignore unknown params fields, Context extension methods included
* **deps:** support google-adk 2.x and wider LangGraph ranges; support Python 3.14
* **chat:** discover A2A and Aion Chat agents together
* **server:** follow a task running on another server through SubscribeToTask, from the stored task and the task journal
* **server:** resume a task paused for input on any server of the agent
* **server:** on a server the Aion platform hosts, accept only push notification URLs that resolve to public addresses, and skip deliveries to any other
* **server:** ExecutorAdapter.discard_undelivered lets a framework executor forget an event the server could not deliver
* **server:** accept several comma-separated keys in ENCRYPTION_KEY: the first encrypts, every key decrypts, so the key can be rotated


### Bug Fixes

* **server:** assign missing task status timestamps before storage, streaming and push delivery, and preserve explicit timestamps
* **server:** keep thinking deltas out of stored tasks
* **server:** wait for the platform WebSocket to actually close
* **server:** say why the Files API refused a file: the refused step (upload or download grant), on whose behalf, and the operation id
* **api:** name the resource Aion reports as missing a daemon identity, such as the deployment and its id, in AionDaemonIdentityRequired
* **server:** fail a file that cannot be stored for lack of a daemon identity as NO_DAEMON_IDENTITY instead of STORAGE_FORBIDDEN: the log line names the step, its behalf and the operation id, and the sender of a rejected inbound file reads that the agent has no daemon identity
* **server:** fail the task when a file the agent produced cannot be stored instead of completing it without the file: what the agent sent beside the file is still delivered, the run stops, a message/send caller gets InternalError (-32603) with the failure's public reason, and the log keeps a WARNING per file with its cause
* **adk:** remove an artifact the server could not store from the ADK artifact service, so a later turn of the context cannot load what the client never received
* **server:** start several servers of an agent together on a fresh PostgreSQL: the permission check before the SDK migrations and the LangGraph checkpoint, ADK session and push notification config table setups take turns under advisory locks instead of failing all but one server, and the push config table is created at startup rather than in the first request
* **server:** keep in-memory ListTasks page tokens valid when the task they name changes or is deleted
* **server:** check the push URL validator before every push delivery
* **server:** load the in-memory task store on Python 3.12 and 3.13
* **chat:** prompt for an explicit login after an authentication failure
* **server:** separate the events of A2A 0.3 streams (message/stream, tasks/resubscribe) with LF, as A2A 1.0 streams are
* **server:** refuse an A2A 0.3 message/stream whose declared extensions fail verification with -32602 before the stream opens
* **server:** report a params parse failure of every method, Context extension methods included, as a2a-sdk 1.2 does: error.data is a list holding a google.rpc.ErrorInfo with the reason in metadata.parseError
* **server:** name the agent card's input and output modes by media type, as A2A requires: aion.yaml takes media types and reads the short names text, json, image, audio and video as text/plain, application/json, image/*, audio/* and video/*
* **server:** check A2A-Version on the Context extension methods (-32009 for another major version; a call without it is served) and acknowledge the extension in A2A-Extensions
