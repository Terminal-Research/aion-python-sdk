# aion.langgraph.server

Server-side LangGraph integration for Aion. Implements `AgentPluginProtocol` — adapts inbound A2A requests into LangGraph `graph.astream()` invocations and maps graph outputs back into A2A Messages, Tasks, and streaming events. Discovered automatically when the `[langgraph-server]` extra is installed.

Installed with `pip install "aionto-sdk[langgraph-server]"`, which also brings `aion.langgraph.authoring` and the server itself.

---

## Setup

Point an agent in `aion.yaml` at the graph. Nothing declares the framework: the path must resolve to a `StateGraph` instance, a compiled `Pregel`, or a callable that returns one, and the adapter claims what it recognises:

```yaml
aion:
  agents:
    my_agent:
      path: "./agent.py:build_graph"
```

See [Quickstart](https://docs.aion.to/sdk/python/quickstart-langgraph) for a full working example.

---

## Inbound

When an inbound A2A `Message` arrives, the server populates the `messages` state field (if declared) and passes the full A2A context to the graph via LangGraph's runtime context mechanism.

### `messages` — LangChain message history

If the graph declares a `messages` field, the server injects a `HumanMessage` built from every part of the inbound A2A message, in order: a text part becomes a text block, an inline or referenced file becomes a file block with its media type, and a structured data part becomes a text block holding its JSON. LangChain has no structured block for a user turn, and text is what every model accepts. The part's metadata is not passed on.

```python
from typing import TypedDict
from langchain_core.messages import BaseMessage

class State(TypedDict):
    messages: list[BaseMessage]
```

### Runtime context — A2A envelope

The full A2A context is passed to the graph via LangGraph's native runtime context and is
accessible through `AionRuntimeContext`. This includes the raw inbox, optional typed event, and
parsed `distribution_extension_payload` with distribution, behavior, environment, principal identity,
and service identity accessors. The recommended way to consume Aion events is to add a
`create_event_router` node from `aion.langgraph.authoring`, which handles injection automatically
while leaving graph edges explicit:

```python
from langgraph.graph import END, START
from aion.langgraph.authoring import create_event_router, Thread, Message

async def handle_message(thread: Thread, message: Message):
    await thread.reply(f"Got: {message.text}")

builder.add_node("aion_events", create_event_router(on_message=handle_message))
builder.add_edge(START, "aion_events")
builder.add_edge("aion_events", END)
```

For lower-level access, declare `runtime` or `context` in a handler signature — see [aion.langgraph.authoring](../authoring/README.md) for the full list of injectable parameters.

---

## Outbound — what the graph's messages become

The server streams the graph in LangGraph's `messages` mode, which carries every message a node produces. Only AI messages with something to show are the agent speaking and reach the client, from whichever node produced them: a model's chunks stream as deltas, and a finished `AIMessage` is a reply. A `ToolMessage`, a `HumanMessage` or `SystemMessage` a node appends, a model turn that only calls a tool, tool-call blocks and reasoning are not sent.

*From whichever node* is meant literally, and it is a deliberate consequence of the contract: the adapter does not know which node speaks for the agent, so the answer of an internal node — a planner, a router, a critic — reaches the client as well, as a stream or as a reply. Tagging the model call `nostream` is not enough to prevent that: it stops the tokens, but LangGraph still emits the `AIMessage` the node returns. To keep a model's output internal, call it with `nostream` **and** keep the result out of the messages — as plain data in a state key of its own:

```python
internal_model = model.with_config(tags=["nostream"])

async def planner(state: State) -> dict:
    plan = await internal_model.ainvoke(state["messages"])
    return {"plan": plan.text}  # a string, not an AIMessage
```

Each model call streams under its own message id, and LangGraph does not repeat a message once its chunks went out. A chunk under a new id therefore ends the previous call's section of `aion:stream-delta`, and the new call opens a section of its own. The streamed text stays that artifact; it is never sent as a reply.

`thread.reply()` with an async iterator returns the whole message that ended its section. A node may keep it in the conversation by returning it into `messages`, as it would any reply; LangGraph then sends that message on the `messages` stream once more, and the server does not send it again.

---

## Outbound — `a2a_outbox`

Return `a2a_outbox` in the graph's output to send an explicit A2A response. The value must be an `A2AOutbox` instance — a Pydantic wrapper that ensures protobuf objects are serializable by LangGraph's checkpoint saver:

```python
from a2a.types import Message, Part, Role
from aion.core.a2a import A2AOutbox

def my_node(state: State) -> State:
    return {
        "a2a_outbox": A2AOutbox(
            message=Message(
                role=Role.ROLE_AGENT,
                parts=[Part(text="Done!")]
            )
        )
    }
```

`A2AOutbox` accepts either `message` or `task`. It answers the run whose node wrote it: the server reads it from that run's own updates, so the value the checkpoint keeps in the channel afterwards is not applied to later turns of the same context. A streamed answer is the `aion:stream-delta` artifact, not a message: a section the run leaves open is closed before the outbox is applied.

With a file storage backend configured, an inline file part (`Part(raw=...)`) anywhere in the outbox - the message, or the task's status message, history and artifacts - is stored and replaced with a URL part before the response is delivered, as every outbound file is. A file that cannot be stored fails the task. The checkpoint still holds the outbox as the node returned it, bytes included; to keep file content out of graph state, send the file with `thread.post(file_artifact(...))` instead.

For full outbound precedence rules, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).

---

## Failing a task

A node that raises ends the turn with the task `FAILED`, and the status message says why, the same on the stream, in the unary answer and in a later `tasks/get`. To give the caller a reason of your own, raise `TaskFailedError`; its text is the message, as written:

```python
from aion.core.exceptions import TaskFailedError

async def forecast(state: State) -> State:
    if not weather_service.available():
        raise TaskFailedError("The weather service is unavailable; try again in a few minutes.")
```

An SDK error that names something the deployment lacks, such as a daemon identity, gives its own sentence the same way. Any other exception is a crash: the task says the agent stopped on an unexpected error and names the task to look up in the log, and the exception's text stays in the log.

## Checkpointing

The plugin configures the graph checkpointer automatically:

- **PostgreSQL** — used when `aion.db` has an active pool. Runs in a dedicated schema (`aion_langgraph`) to avoid collisions with application tables.
- **In-memory** — used when no database is configured (state is lost on process restart).

No configuration is required. Schema setup and migrations run on first startup. If `POSTGRES_URL` is set but the checkpointer cannot be initialized, startup fails.

A checkpoint belongs to one agent and one user. Its `thread_id` is not the A2A `context_id` alone but `aion.v1:<agent id>:<owner>:<context id>`, each part percent-encoded, where the owner is what the agent's `owner_resolver` names for the request's `ServerCallContext` - by default its trusted user, and always the same `owner_scope` the tasks table records. A client chooses its `context_id`, so two users, or two agents sharing one database, may present the same one; they get separate threads. The A2A `context_id` the client sees does not change. `stream` and `resume` both read and write through this key, so another user's message on the same `context_id` cannot answer, or read, a graph paused for someone else.

Checkpoints saved before this key existed sit under the bare `context_id` and cannot be attributed to a user. They are neither read nor replaced: a turn on such a context fails with `LegacyStateError`, naming the context, and the old checkpoint is left in place until it is migrated or removed.

This applies to a graph returned uncompiled, and to one returned already compiled without a checkpointer of its own — what `langchain.agents.create_agent(model, tools)` gives you. The server needs a checkpointer to run a graph at all: it reads the graph's state at the end of every turn, and memory between turns and `interrupt()` depend on it. A graph compiled with its own checkpointer keeps it, and then its state lives wherever that checkpointer keeps it rather than in the server's database. A graph compiled with `checkpointer=False` is left as it is and cannot finish a turn under the server.

---

## Interrupts and Resume

LangGraph `interrupt()` calls are mapped to `TaskStatusUpdateEvent(INPUT_REQUIRED)`. The client resumes the graph by sending a new A2A message to the same task, which is forwarded as a `Command(resume=...)` to LangGraph.

The value `interrupt()` returns on resume is the same graph input the server builds for any turn — `{"messages": [HumanMessage(...)]}`, holding the resuming A2A message as described under [Inbound](#inbound) — not the bare answer:

```python
from langgraph.types import interrupt

def review_node(state: State) -> State:
    resume = interrupt("Please confirm the action.")
    # execution resumes here; the user's reply is the HumanMessage inside
    answer = resume["messages"][0].text
    return {**state, "confirmation": answer}
```

The interrupt value becomes the question the client sees in the `INPUT_REQUIRED` status, and the task keeps its id: the client resumes by sending the answer with the same `task_id` and `context_id`.

---

## Plugin Discovery

`aion.server` discovers this plugin at startup via dynamic import:

```python
# aion.server resolves this automatically; the extra decides whether it imports
"aion.langgraph.server.LangGraphPlugin"
```

Without the `[langgraph-server]` extra the import fails on LangGraph itself, and the plugin is skipped — the skip is recorded with the name of the extra, so an agent that cannot be built says which install is missing. No configuration or registration is required.

---

## Architecture

```
aion.server
  └── discovers LangGraphPlugin (aion.langgraph.server)
        ├── LangGraphAdapter       — compiles graphs, creates executors
        ├── LangGraphExecutor      — orchestrates stream / resume lifecycle
        │   ├── StreamExecutor     — calls graph.astream(), yields A2A events
        │   ├── EventConverter     — maps LangGraph events → A2A protocol events
        │   └── ResultHandler      — produces terminal events from a2a_outbox or delta text
        ├── CheckpointerFactory    — creates PostgreSQL or memory checkpointer
        └── Converters             — bidirectional A2A ↔ LangChain content block conversion
```
