# LangGraph Configuration

This document explains how `_langgraph_api` loads graphs from `langgraph.json` at startup so you can replicate the behaviour in your own server implementation.

## Loading graphs

1. **Read `langgraph.json`**
   - The CLI entrypoint reads the configuration file specified with `--config` (default `langgraph.json`).
   - The file is parsed with `json.load()` and relevant sections are extracted.
   - Example from `cli.py`:
     ```python
     with open(args.config, encoding="utf-8") as f:
         config_data = json.load(f)
     graphs = config_data.get("graphs", {})
     auth = config_data.get("auth")
     run_server(..., graphs, env=config_data.get("env", None), auth=auth)
     ```

2. **Set environment variables**
   - `run_server()` patches environment variables before launching the ASGI app.
   - The `graphs` mapping is serialized to JSON and written to `LANGSERVE_GRAPHS`.
   - Other sections like `store`, `auth`, and `http` are written to `LANGGRAPH_STORE`, `LANGGRAPH_AUTH`, and `LANGGRAPH_HTTP` respectively.
   - Snippet:
     ```python
     with patch_environment(
         LANGSERVE_GRAPHS=json.dumps(graphs) if graphs else None,
         LANGGRAPH_STORE=json.dumps(store) if store else None,
         LANGGRAPH_AUTH=json.dumps(auth) if auth else None,
         LANGGRAPH_HTTP=json.dumps(http) if http else None,
         ...
     ):
         uvicorn.run("langgraph_api.server:app", ...)
     ```

3. **Initialize graphs on startup**
   - During application startup, `lifespan.lifespan()` calls `collect_graphs_from_env(True)`.
   - This function reads `LANGSERVE_GRAPHS` and optional per-graph config from `LANGGRAPH_CONFIG`.
   - Each referenced module is imported and the graph object or factory is registered via `register_graph()`.
   - Relevant excerpt:
     ```python
     await collect_graphs_from_env(True)
     ```

## `langgraph.json` properties

The configuration file can contain several sections. `_langgraph_api` primarily uses the following:

- **`graphs`** – Required. A mapping of graph IDs to import strings (`"path/to/file.py:object"`). These are serialized to `LANGSERVE_GRAPHS` so the server knows which graphs to load.
- **`env`** – Optional. Environment variables or a path to a `.env` file. Values are applied when patching the environment so your graphs have access to them.
- **`auth`** – Optional. Custom authentication config. Stored in `LANGGRAPH_AUTH` and later merged into the OpenAPI spec.
- **`http`** – Optional. HTTP server options such as custom routes or CORS settings. Stored in `LANGGRAPH_HTTP` for initialization.
- **`store`** – Optional. Configuration for the object store and vector index. Written to `LANGGRAPH_STORE` so the storage layer is set up accordingly.

By following this flow—read the JSON file, export the properties to environment variables, and then call `collect_graphs_from_env()`—you can emulate how `_langgraph_api` discovers and registers graphs when the server starts.
