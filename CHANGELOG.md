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


### Bug Fixes

* **server:** keep thinking deltas out of stored tasks
* **server:** wait for the platform WebSocket to actually close
* **server:** say why a Files API upload failed
* **server:** keep in-memory ListTasks page tokens valid when the task they name changes or is deleted
* **server:** check the push URL validator before every push delivery
* **server:** load the in-memory task store on Python 3.12 and 3.13
* **chat:** prompt for an explicit login after an authentication failure
* **server:** separate the events of A2A 0.3 streams (message/stream, tasks/resubscribe) with LF, as A2A 1.0 streams are
* **server:** refuse an A2A 0.3 message/stream whose declared extensions fail verification with -32602 before the stream opens
* **server:** report a params parse failure of every method, Context extension methods included, as a2a-sdk 1.2 does: error.data is a list holding a google.rpc.ErrorInfo with the reason in metadata.parseError
* **server:** name the agent card's input and output modes by media type, as A2A requires: aion.yaml takes media types and reads the short names text, json, image, audio and video as text/plain, application/json, image/*, audio/* and video/*
* **server:** check A2A-Version on the Context extension methods (-32009 for another major version; a call without it is served) and acknowledge the extension in A2A-Extensions
