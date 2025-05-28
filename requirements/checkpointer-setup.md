# Checkpointer Setup in LangGraph API

This document explains how `langgraph_api` configures checkpointing for each graph when serving projects. The API server automatically assigns a `Checkpointer` instance to every graph and users **should not** create their own when using the API.

## Default Checkpointer

The in-memory implementation resides in the [langgraph-storage](https://github.com/langchain-ai/langgraph-storage) project, which this repository includes under `libs/_langgraph_storage` for convenience. A global `InMemorySaver` is created and exposed via the `Checkpointer()` function:

```python
MEMORY = InMemorySaver()

def Checkpointer(*args, **kwargs):
    return MEMORY
```

The saver persists data under `.langgraph_api/.langgraph_checkpoint.*.pckl` and is cleared when the server shuts down.

## Injecting the Checkpointer

When a request is processed, the API opens a connection to the local storage backend (`langgraph_storage.database.connect`). For each run it calls `get_graph` and passes a `Checkpointer` bound to that connection:

```python
async with get_graph(
    config["configurable"]["graph_id"],
    config,
    store=Store(),
    checkpointer=None if temporary else Checkpointer(conn),
) as graph:
    ...
```

This snippet is taken from `libs/_langgraph_api/stream.py` and shows how the checkpointer is constructed for every run. The `get_graph` helper then attaches this instance to the graph:

```python
update = {
    "checkpointer": checkpointer,
    "store": store,
}
if graph_obj.name == "LangGraph":
    update["name"] = graph_id
yield graph_obj.copy(update=update)
```

The graph therefore receives a ready-to-use checkpointer without any user configuration.

## JS Graphs

When JavaScript graphs are loaded via `collect_graphs_from_env`, the server spawns a background task that runs `run_remote_checkpointer`. This task exposes HTTP endpoints that proxy to the same Python `Checkpointer` so that JS graphs can checkpoint remotely.

## PostgreSQL Checkpointer

LangGraph can persist checkpoints in a Postgres database using the `langgraph-checkpoint-postgres` package. To set it up:

1. Install the dependencies:

   ```bash
   pip install langgraph-checkpoint-postgres psycopg[binary]
   ```

2. Initialize a `PostgresSaver` and create the tables:

   ```python
   from langgraph.checkpoint.postgres import PostgresSaver

   DB_URI = "postgresql://user:password@localhost:5432/dbname"
   checkpointer = PostgresSaver.from_conn_string(DB_URI)
   checkpointer.setup()  # Creates necessary tables
   ```

3. Compile your graph with this checkpointer:

   ```python
   graph = builder.compile(checkpointer=checkpointer)
   ```

4. Invoke the graph with a thread ID:

   ```python
   config = {"configurable": {"thread_id": "1"}}
   graph.invoke(input_data, config)
   ```

Using PostgreSQL lets your LangGraph application persist checkpoints across sessions, enabling durable memory and features like semantic search when `pgvector` is installed.

