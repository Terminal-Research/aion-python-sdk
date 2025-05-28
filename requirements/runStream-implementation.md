# runStream Implementation Requirements

The following requirements outline how to build a `runStream` endpoint that mirrors the behaviour of `_langgraph_api`.

## Functional Requirements

- **Run Creation**
  - Generate a unique `run_id` and associate it with a `thread_id` and `assistant_id` supplied by the client.
  - Collect request headers, body parameters, and optional `checkpoint_id` to build the runtime configuration. Store these details with the run record.
  - Persist the run using a storage backend similar to `Runs.put`.

- **Queue Subscription**
  - Create an in-memory queue for each run. Subscribers should receive published events in order.
  - Allow the client to specify a stream mode (e.g., event stream vs. state stream) when subscribing.

- **Streaming Response**
  - Return an HTTP streaming response (e.g., Server-Sent Events) that yields messages from the run's queue via a `join` method.
  - Include a `Location` header pointing to `/threads/{thread_id}/runs/{run_id}/stream`.

- **Run Execution**
  - Launch the graph execution in a background task. The worker should:
    - Configure the LangGraph with the provided state, checkpointer, and store.
    - Publish events (state updates, messages, checkpoints) to the run's queue.
    - Signal completion by sending a `done` control message.

- **Checkpointing and Side Effects**
  - Use a checkpointer to persist graph state so that runs can be resumed or joined later.
  - Persist side effects (messages, intermediate state) in storage.

- **Interrupts and Cancellation**
  - Listen for control messages (e.g., `interrupt`, `rollback`, `cancel`) from the client or server to stop execution.
  - Propagate these interrupts to the running graph and publish final state.

## Non-Functional Requirements

- Implement the API in an idiomatic asynchronous Python style using `async`/`await`.
- Document all functions and classes with clear docstrings.
- Provide unit tests for queue management, streaming behaviour, and run lifecycle.

## Step-by-Step Task List

1. **Design Request Schema**
   - Define the payload for `runStream` including `assistant_id`, `thread_id`, configuration, and optional checkpoint information.

2. **Implement Run Record Creation**
   - Write a helper similar to `create_valid_run` that stores run metadata and validates inputs.

3. **Set Up Streaming Infrastructure**
   - Implement a `Stream` manager that can `subscribe`, `publish`, and `join` queues per run.
   - Ensure `join` yields events until a `done` control message is received.

4. **Implement the Endpoint**
   - Create the HTTP handler `runStream` that:
     - Calls the run creation helper.
     - Subscribes to the run's queue.
     - Launches the worker task to execute the graph.
     - Returns an event stream response using `join` to yield data.

5. **Worker Execution Logic**
   - Write a background worker that sets up the LangGraph, handles checkpoints, and publishes events.
   - Ensure it responds to interrupt or rollback messages and signals completion.

6. **Checkpoint and State Persistence**
   - Integrate a `Checkpointer` to save graph state to disk or another persistent store.
   - Store run events and metadata for later retrieval.

7. **Cancellation Handling**
   - Provide a mechanism to cancel or rollback runs via control messages on the queue and propagate this to the worker.

8. **Testing**
   - Write unit tests covering:
     - Run creation and queue subscription.
     - Streaming response behaviour.
     - Checkpoint persistence and replay.
     - Interrupt and cancellation logic.

