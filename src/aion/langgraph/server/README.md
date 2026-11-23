# aion.langgraph.server

Server-side LangGraph integration for Aion. Implements `AgentPluginProtocol` — adapts inbound A2A requests into LangGraph `graph.astream()` invocations and maps graph outputs back into A2A Messages, Tasks, and streaming events. Discovered automatically when the `[langgraph-server]` extra is installed.

Installed with `pip install "aionto-sdk[langgraph-server]"`, which also brings `aion.langgraph.authoring` and the server itself.

---

## Setup

Point an agent in `aion.yaml` at the graph. Nothing declares the framework: the path must resolve to a `StateGraph` instance, a compiled `Pregel`, or a callable that returns one, and the adapter claims what it recognises. The callable may be an `async def`: the server awaits it once at startup, on its own event loop, so a factory can load MCP tools or open a connection while it builds the graph:

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

If the graph declares a `messages` field, the server injects a `HumanMessage` built from every part of the inbound A2A message, in order: a text part becomes a text block, a file becomes the block LangChain has for its kind - an image, audio or video block by its media type, a file block otherwise - with its media type and its name, and a structured data part becomes a text block holding its JSON. LangChain has no structured block for a user turn, and text is what every model accepts. The part's metadata is not passed on. A graph whose input has no `messages` field gets nothing from this: LangGraph drops the key, and the graph reads the message from the turn's context, `Thread.from_context(runtime.context).message`. The server says so in its log at startup.

On a deployment that stores files, a file block holds the file's address, not its bytes, and that is what the graph's state and its checkpoints keep. The block names the stored file too, under `aion:file` in `extras`, and the file is read by that name, through a link issued for the turn's sender. A model is sent the content: `aion_chat_openai` and `aion_chat_model` replace every file block that carries an address with the file's content in each request, and a graph with a model of its own calls `inline_files` on the messages it sends (`aion.langgraph.authoring.files`). The address is that of the deployment's storage, which works only with its authorization, so a provider cannot fetch it, and OpenAI's Chat Completions takes no file by address at all. A turn reads each file once, and the files of one model request at once: what a turn reads and stores is kept for the turn and goes with it (`aion.core.a2a.file_contents`), and the files its request brought are not downloaded back. A later turn reads a file from storage again, for as long as the file is kept. Every file of the messages is sent on every call, so a graph that should stop sending an old file leaves its message out. A file goes with its own media type; which types a model takes is for the model service and the provider to decide, and the SDK keeps no list of its own. The message with a file stays in the graph's state, so a file the model does not take would fail every later call too. The model `aion_chat_openai` and `aion_chat_model` build leaves such a file out (`aion.core.agent.left_out_files`): a call the model refuses on what its request carries - status 400, 413, 415 or 422 from the model service - Aion's model service answers with HTTP 200 and a stream, and names the provider's status at the start of the stream's `api_error` event - or a block LangChain cannot send, such as a video - is made again without one file, the newest first, and when no single file explains the refusal, each file is checked alone in a short request, streamed whatever the call is: the service answers only with a stream. A text note takes the place of each file the model refused, `[File "clip.mp4" (video/mp4) was not accepted by the model and was left out]`, and later calls to the same model leave it out from the start, for as long as the server runs; a warning in the log names the file and the model. Only the call that goes through reaches the graph's callbacks and the client; the state keeps the messages as they were. A refusal no file explains, and any other failure, fails the call as before, and the error, left uncaught, fails the task with a reason that says the agent stopped on an unexpected error. A graph with a model of its own sees the refusal as its model's error: it catches the error in its node and replaces the message with the file, under the same id, with one without it. A file larger than `AION_MODEL_FILE_MAX_BYTES` (default 20 MiB) is not read: a note says so in its place, `inline_files` included. A file the storage does not make available to the turn's sender - in a conversation several people share, one another participant stored - is not read either: `[File "a.pdf" (application/pdf) is not available to this sender and was left out]` takes its place, and the turn goes on. A file that is no longer stored is not read either: the SDK stores the files of a conversation for 30 days from their upload, and once the retention deadline its reference records has passed, or the storage says the file no longer exists, `[File "report.pdf" (application/pdf) is no longer available and was left out]` takes its place, and the turn goes on; a warning in the log names the file once a turn. Any other file that cannot be read fails the task and says so. A message may name a file by any address, so a file is downloaded from public addresses only, every redirect included: a host that resolves to a private, loopback, link-local or other non-public address is refused, and the connection goes to the address that was checked (`aion.core.http.file_transport`). The Aion API host (`AION_API_HOST`) is always allowed; `AION_FILE_TRUSTED_HOSTS` lists other hosts, comma-separated, that an agent is meant to reach on an internal address. These downloads take no proxy from the environment.

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

Each model call streams under its own message id, and LangGraph does not repeat a message once its chunks went out. A call's section of `aion:stream-delta` ends on the chunk langchain-core marks last, and the next call opens a section of its own. The streamed text stays that artifact; it is never sent as a reply.

A task has one streamed answer at a time, so model calls that run at once - parallel nodes, `Send`, subgraphs side by side - take turns. The call that started first streams live; the chunks of the others are held, and each follows as a section of its own once the one before it has ended, in the order the calls started. A `thread.reply()` stream beside a model call waits the same way. Every answer reaches the client whole. The task keeps the section that ended last, as it does after calls made one after another: that is the call that *started* last, whichever finished first. A graph whose branches each stream an answer should end in a node that gives the turn's answer, or have the branches reply with whole messages, which the task keeps all of. A message, card or artifact a node sends while another node's call is streaming goes out at once and leaves that section alone.

The graph is streamed together with its subgraphs, so *whichever node* includes the nodes of a subgraph, and of a graph a node invokes itself: a model call there streams as deltas while it runs, and `thread.reply()` and the `emit_*` calls deliver from there as they do from a node of the parent graph. The `a2a_outbox` the server reads is the parent graph's: a subgraph that shares the key hands it over when its node returns.

`thread.reply()` with an async iterator returns the whole message that ended its section. A node may keep it in the conversation by returning it into `messages`, as it would any reply; LangGraph then sends that message on the `messages` stream once more, and the server does not send it again.

The iterator may be a model's stream - `model.astream(...)`, or text taken from it. `thread.reply()` streams it with `aemit_message`, and `emit_message` streams a sync iterator the same way: the model calls that start while the iterator is read stay off the `messages` stream, so the answer goes out once, as the thread's section with the reply's routing target, and a call whose result the iterator does not yield does not reach the client. A call that started before the iterator is read - the node read the iterator before passing it, or a task started earlier feeds it - and one given `tags` in a config of its own stay on the `messages` stream. For an iterator of that call's chunks the server sends the call from there and leaves out the copy, the whole message that ends it included: that message takes the id the chunks share. Text taken from such a call carries no id to know it by, and the answer reaches the client twice; pass the iterator to `thread.reply()` unread.

A node that emits the chunks of a model call LangGraph streams, or the message the call returned, sends such a copy as well. LangGraph sends a call's chunks on the `messages` stream before the node gets them, so the copy always comes second: the server sends no event for its chunks, and its whole message ends the call's section as the call's own closing message does - its text is not sent again, and what else it carries goes out as a message. The routing target and the metadata of the copy's chunks are not sent.

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

Declare the field with `A2AOutboxField` from `aion.langgraph.authoring`:

```python
from aion.langgraph.authoring import A2AOutboxField

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    a2a_outbox: A2AOutboxField
```

`A2AOutbox` accepts either `message` or `task`. It answers the run whose node wrote it: the server reads it from that run's own updates, never from the checkpoint. `A2AOutboxField` puts the field on LangGraph's `UntrackedValue` channel, so the value is not checkpointed at all: a later turn does not find it in its state, and the checkpoint holds no `A2AOutbox`, a type LangGraph's serializer does not know and warns it will refuse to load. A field declared as plain `Optional[A2AOutbox]` or `A2AOutbox` gets LangGraph's ordinary channel, and the server gives it the untracked one in the graph it serves - one it compiles, or a compiled graph it copies to attach its checkpointer, or one compiled with a checkpointer of its own - so the field behaves as `A2AOutboxField` there, and the server logs once at startup which field it changed. A checkpoint written before that keeps its outbox until the thread's next turn writes a new one. A field with a reducer of its own is left alone, and so is the graph of a subgraph node and a graph run outside the server: there the checkpoint keeps the outbox with that warning; with a file storage backend its files are kept as the addresses of the stored files (see [Files in checkpoints](#files-in-checkpoints)). When the server copies a compiled graph, the copy keeps the msgpack allowlist LangGraph built from the graph's state schema. A Task in the outbox is the agent's: its state is the turn's outcome when it decides the turn - a terminal state, or `INPUT_REQUIRED` that the next message continues as an ordinary turn - and the server builds no `aion:assistant-answer` for it. A streamed answer is the `aion:stream-delta` artifact, not a message: a section the run leaves open is closed before the outbox is applied.

With a file storage backend configured, an inline file part (`Part(raw=...)`, or a `data:` URL) anywhere in the outbox - the message, or the task's status message, history and artifacts - is stored and replaced with a URL part before the response is delivered, as every outbound file is. A file that cannot be stored fails the task.

For full outbound precedence rules, see [Message Mapping](https://docs.aion.to/sdk/python/frameworks/langgraph/message-mapping).

---

## Outbound — `structured_response`

An agent built by `langchain.agents.create_agent` with a `response_format` answers with an object of that schema, which LangChain puts in the graph's `structured_response`. The server sends it as an artifact of the task named `structured_response`: one data part holding the object as JSON, as Pydantic dumps it - a model, a dataclass, a dict, an enum, a date - with NaN and infinity as `null`. A value that has no JSON form fails the task, with a reason that says so.

```python
from pydantic import BaseModel
from langchain.agents import create_agent

class Forecast(BaseModel):
    city: str
    degrees: int

agent = create_agent(model, tools=[get_weather], response_format=Forecast)
# The client receives an artifact named "structured_response":
# one data part, {"city": "Kyiv", "degrees": 5}
```

How the model answers in the schema decides what else the client sees. With `ToolStrategy` the model calls the schema's tool; that call and the `ToolMessage` closing it are not sent, so the artifact is the whole answer. With `ProviderStrategy` the model's text is the JSON itself, and it streams as `aion:stream-delta` as any answer does: the task holds both, the text for a reader and the object for code. A schema passed as it is lets LangChain choose by the model: `ProviderStrategy` for a model with structured output of its own, `ToolStrategy` otherwise. The artifact is a result of the task, so a task with one gets no `aion:assistant-answer`.

The server reads the value the way it reads `a2a_outbox`: from what a node of the parent graph wrote during the run, never from the checkpoint, which keeps it across turns. Each turn that produces an object sends its own. A graph of your own that writes `structured_response` sends it the same way, so keep a value meant for the graph alone under another key. An agent that runs as a node of a larger graph hands its object over only if the parent's state has the key. A run that writes an `a2a_outbox` answers with the outbox, and the structured response is not sent.

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

### Files in checkpoints

With a file storage backend configured, a checkpoint never holds a file's bytes. A message in the state can carry a file inline - a node or a model returns an `AIMessage` with a `base64` block, a tool answers with a `ToolMessage` holding an image - and the checkpointer would save it in every version of the state and every pending write. The message may be anywhere LangGraph saves it: in a list, a tuple or a dict, subclasses and named tuples included, in a Pydantic model or a dataclass the state holds, in the argument of a `Send` or in the value of an `interrupt()`. The server's checkpointers store such a file first and save the block with the stored file's address in its place: the block keeps its kind, media type and name, holds the address in `url` - it works only with authorization, so the checkpoint holds no credential - and names the stored file under `aion:file` in `extras`, by which the SDK's models read it later through a link issued for the turn's sender (`inline_files`). A block in the older form (`source_type: "base64"`) or in OpenAI's chat format (a `data:` URL in `image_url`) is saved in the standard form. The files of an `A2AOutbox` kept in an ordinary state field are stored the same way. What is saved is a copy, of the same type as what it copies. A graph compiled with a checkpointer of its own is served with a copy of that checkpointer whose class adds the same linking, over the same storage (`checkpoint.files.linking_files`); the graph and the checkpointer it was given are not changed. A checkpointer that cannot be copied so is kept as it is, with a warning at startup, and its checkpoints keep the bytes.

A file the turn also sends to the client is stored once: the task and the checkpoint name the same stored file, whichever of the two asks first. A file whose content the turn read from a stored file is not stored again either: a graph that returns into its state the messages `inline_files` gave it for a model request - copies under the same ids, with the content in place of the address - gets the stored file it read linked back, with its own reference and retention (`aion.server.files.turn`). Only when the turn read several stored files with the same bytes is the content stored, since which of them it came from cannot be told. Return only the model's answer into the state all the same: the copies carry the content for the rest of the run. What the graph holds while the turn runs is not changed - the run goes on with the bytes it has, and a model later in the same turn is sent them as they are. A file that cannot be stored fails the task, and the checkpoint is not written with the bytes instead. Without a file storage backend, a checkpoint keeps the bytes.

A checkpoint belongs to one agent and one user. Its `thread_id` is not the A2A `context_id` alone but `aion.v1:<agent id>:<owner>:<context id>`, each part percent-encoded, where the owner is what the agent's `owner_resolver` names for the request's `ServerCallContext` - by default its trusted user, and always the same `owner_scope` the tasks table records. A client chooses its `context_id`, so two users, or two agents sharing one database, may present the same one; they get separate threads. The A2A `context_id` the client sees does not change. `stream` and `resume` both read and write through this key, so another user's message on the same `context_id` cannot answer, or read, a graph paused for someone else.

Checkpoints saved before this key existed sit under the bare `context_id` and cannot be attributed to a user. They are neither read nor replaced: a turn on such a context fails with `LegacyStateError`, naming the context, and the old checkpoint is left in place until it is migrated or removed.

This applies to a graph returned uncompiled, and to one returned already compiled without a checkpointer of its own — what `langchain.agents.create_agent(model, tools)` gives you. The server needs a checkpointer to run a graph at all: it reads the graph's state at the end of every turn, and memory between turns and `interrupt()` depend on it. A graph compiled with its own checkpointer keeps its storage, and then its state lives wherever that checkpointer keeps it rather than in the server's database; files are saved there as links all the same (see [Files in checkpoints](#files-in-checkpoints)). A graph compiled with `checkpointer=False` is left as it is and cannot finish a turn under the server.

---

## Interrupts and Resume

LangGraph `interrupt()` calls are mapped to `TaskStatusUpdateEvent(INPUT_REQUIRED)`. The client resumes the graph by sending a new A2A message to the same task, and `interrupt()` returns the user's answer, as LangGraph documents it:

```python
from langgraph.types import interrupt

def review_node(state: State) -> State:
    answer = interrupt("Deploy to production?")
    # execution resumes here with the answer itself
    return {**state, "approved": answer == "yes"}
```

The answer is read by the rule both adapters follow (`aion.core.a2a.reply_of`):

- a data part is the answer as it is - a dict, a list, a number - so `interrupt(...)["decisions"]` works, and so does `HumanInTheLoopMiddleware`, whose `{"decisions": [...]}` a client sends as a data part;
- without one, the `actionId` of a pressed card button is the answer, as a string;
- otherwise the answer is the message's text, a string; JSON written in it stays text;
- the parts a distribution adds on its own - those whose metadata carries the event extension, such as `MessageEventPayload` and `SourceSystemEventPayload` on a message from Slack - are not the answer; the node reads them from the event of its runtime context;
- files sent with the answer do not change it: they are the files of the turn's message, read as on any turn (see [Files of the Answer](#files-of-the-answer));
- a message with more than one data part of its own, or whose answer does not match the question's `responseSchema` (below), is refused with `invalid_payload` before the graph runs, and the task keeps waiting.

The graph resumes by the interrupt's id, `Command(resume={interrupt_id: answer})`, even when one interrupt waits: LangGraph takes a dict whose keys all look like interrupt ids - an empty one among them - for a map of answers by id.

The interrupt value becomes the question the client sees in the `INPUT_REQUIRED` status, and the task keeps its id: the client resumes by sending the answer with the same `task_id` and `context_id`. The question's text part is a string value itself, or a dict's `"prompt"`, or "Agent requires input". A value that is not a plain string follows the text whole as a data part, as JSON with NaN and infinity as `null`, so the client sees what the graph asks about - the options of a choice, or the actions `HumanInTheLoopMiddleware` asks to review. A value with no JSON form leaves the question as text alone, with a warning.

The question's message carries the interrupt's id as `interruptId` in its metadata. Several nodes of one step may interrupt at once; the questions are then asked one per turn, in the order LangGraph reports them, and each answer resumes one interrupt while the others keep waiting. An answer resumes the interrupt its message names under `interruptId` in the message's metadata, and the one asked without it. An answer that names an interrupt which no longer waits does not resume the graph: the question that waits is asked again.

### Static Breakpoints

A graph compiled with `interrupt_before` or `interrupt_after` stops at those nodes with nothing to ask, and LangGraph goes on when it is invoked again with no input. The task waits for input as well: the question's text is `Paused before <node>. Send any message to continue.`, its data part `{"next": [...]}` names the nodes the graph runs next, as LangGraph's state snapshot does, and it carries no `interruptId` and no `responseSchema`. Any message continues the graph with no input, as `graph.invoke(None, config)` does; its content does not reach the graph, so a reply of "no" goes on too. A graph that should ask the user something uses `interrupt()` or `HumanInTheLoopMiddleware`.

### The Schema of the Answer

When the graph states what the answer must be, the question's metadata carries that JSON Schema as `responseSchema`, so a client knows how to answer - a string as text, anything else as a data part - and the server refuses an answer that does not match it before the graph runs:

- the schema given to `interrupt(value, response_schema=...)`, as LangGraph keeps it: a JSON Schema `dict` as it is, a Pydantic model, `TypedDict` or dataclass converted to JSON Schema. `interrupt()` takes it from LangGraph 1.2.12; on older releases the question carries none;
- `HumanInTheLoopMiddleware` in its default batched mode: the schema of its `{"decisions": [...]}` answer, built from LangChain's own decision types and the question's `review_configs` - exactly one decision per tool call, each one its tool allows. In its `per_call` mode the middleware states a schema of its own, which goes as it is;
- any other `interrupt()`: none, and the answer is whatever the client sends.

A Pydantic model, `TypedDict` or dataclass also makes LangGraph check the answer itself, and `interrupt()` returns the validated object. An answer LangGraph refuses that way - a validator of the model the JSON Schema cannot express, say - is not saved, and the question is asked again; the task keeps waiting. LangGraph marks such a refusal from 1.2.15; before it the task fails.

```python
from pydantic import BaseModel
from langgraph.types import interrupt


class Deploy(BaseModel):
    environment: str
    confirmed: bool


def deploy_node(state: State) -> dict:
    answer = interrupt("Where should I deploy?", response_schema=Deploy)  # a Deploy instance
    return {"environment": answer.environment, "approved": answer.confirmed}
```

### Files of the Answer

The answer resumes the graph in a turn of its own, whose message is the answer's message. A node reads its files the way it reads a turn's files, from the thread of its runtime context:

```python
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from aion.core.runtime import AionRuntimeContext
from aion.langgraph.authoring import Thread


async def review_node(state: State, runtime: Runtime[AionRuntimeContext]) -> dict:
    answer = interrupt("Send the signed contract and confirm")
    files = Thread.from_context(runtime.context).message.files
    contract = (await files[0].read()).decode() if files else ""
    return {"approved": answer == "yes" and "Signed:" in contract}
```

LangGraph runs the interrupted node again from its start when the answer resumes it, so the code before `interrupt()` runs twice: once with the turn's message that led to the question, once with the answer. A node reads the answer's files after `interrupt()` returns. The files do not enter the graph's state on their own; a node that keeps them, or hands them to a model, puts them into its state or its model call itself.

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
        │   └── ResultHandler      — produces result events from a2a_outbox or structured_response
        ├── CheckpointerFactory    — creates PostgreSQL or memory checkpointer
        └── Converters             — bidirectional A2A ↔ LangChain content block conversion
```
