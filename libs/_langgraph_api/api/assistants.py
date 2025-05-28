from typing import Any
from uuid import uuid4

import structlog
from langchain_core.runnables.utils import create_model
from langgraph.pregel import Pregel
from pydantic import TypeAdapter
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import BaseRoute

from langgraph_api.graph import get_assistant_id, get_graph
from langgraph_api.js.base import BaseRemotePregel
from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.serde import ajson_loads
from langgraph_api.utils import fetchone, validate_uuid
from langgraph_api.validation import (
    AssistantCreate,
    AssistantPatch,
    AssistantSearchRequest,
    AssistantVersionChange,
    AssistantVersionsSearchRequest,
)
from langgraph_storage.database import connect
from langgraph_storage.ops import Assistants
from langgraph_storage.retry import retry_db

logger = structlog.stdlib.get_logger(__name__)


def _get_configurable_jsonschema(graph: Pregel) -> dict:
    """Get the JSON schema for the configurable part of the graph.

    Important: we only return the `configurable` part of the schema.

    The default get_config_schema method returns the entire schema (RunnableConfig),
    which includes other root keys like "max_concurrency", which we
    do not want to expose.

    Args:
        graph: The graph to get the schema for.

    Returns:
       The JSON schema for the configurable part of the graph.
    """
    # Otherwise, use the config_schema method.
    config_schema = graph.config_schema()

    if "configurable" in config_schema.__fields__:
        configurable = TypeAdapter(config_schema.__fields__["configurable"].annotation)
        json_schema = configurable.json_schema()
        # The type name of the configurable type is not preserved.
        # We'll add it back to the schema if we can.
        if hasattr(graph, "config_type") and graph.config_type is not None:
            if hasattr(graph.config_type, "__name__"):
                json_schema["title"] = graph.config_type.__name__
            return json_schema

    # If the schema does not have a configurable field, return an empty schema.
    return {}


def _state_jsonschema(graph: Pregel) -> dict | None:
    fields: dict = {}
    for k in graph.stream_channels_list:
        v = graph.channels[k]
        try:
            create_model(k, __root__=(v.UpdateType, None)).schema()
            fields[k] = (v.UpdateType, None)
        except Exception:
            fields[k] = (Any, None)
    return create_model(graph.get_name("State"), **fields).schema()


def _graph_schemas(graph: Pregel) -> dict:
    try:
        input_schema = graph.get_input_jsonschema()
    except Exception as e:
        logger.warning(
            f"Failed to get input schema for graph {graph.name} with error: `{str(e)}`"
        )
        input_schema = None
    try:
        output_schema = graph.get_output_jsonschema()
    except Exception as e:
        logger.warning(
            f"Failed to get output schema for graph {graph.name} with error: `{str(e)}`"
        )
        output_schema = None
    try:
        state_schema = _state_jsonschema(graph)
    except Exception as e:
        logger.warning(
            f"Failed to get state schema for graph {graph.name} with error: `{str(e)}`"
        )
        state_schema = None
    try:
        config_schema = _get_configurable_jsonschema(graph)
    except Exception as e:
        logger.warning(
            f"Failed to get config schema for graph {graph.name} with error: `{str(e)}`"
        )
        config_schema = None
    return {
        "input_schema": input_schema,
        "output_schema": output_schema,
        "state_schema": state_schema,
        "config_schema": config_schema,
    }


@retry_db
async def create_assistant(request: ApiRequest) -> ApiResponse:
    payload = await request.json(AssistantCreate)
    """Create an assistant."""
    if assistant_id := payload.get("assistant_id"):
        validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant = await Assistants.put(
            conn,
            assistant_id or str(uuid4()),
            config=payload.get("config") or {},
            graph_id=payload["graph_id"],
            metadata=payload.get("metadata") or {},
            if_exists=payload.get("if_exists") or "raise",
            name=payload.get("name") or "Untitled",
        )
    return ApiResponse(await fetchone(assistant, not_found_code=409))


@retry_db
async def search_assistants(
    request: ApiRequest,
) -> ApiResponse:
    """List assistants."""
    payload = await request.json(AssistantSearchRequest)
    async with connect() as conn:
        assistants_iter = await Assistants.search(
            conn,
            graph_id=payload.get("graph_id"),
            metadata=payload.get("metadata"),
            limit=payload.get("limit") or 10,
            offset=payload.get("offset") or 0,
        )
    return ApiResponse([assistant async for assistant in assistants_iter])


@retry_db
async def get_assistant(
    request: ApiRequest,
) -> ApiResponse:
    assistant_id = request.path_params["assistant_id"]
    """Get an assistant by ID."""
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant = await Assistants.get(conn, assistant_id)
    return ApiResponse(await fetchone(assistant))


@retry_db
async def get_assistant_graph(
    request: ApiRequest,
) -> ApiResponse:
    """Get an assistant by ID."""
    assistant_id = get_assistant_id(str(request.path_params["assistant_id"]))
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant_ = await Assistants.get(conn, assistant_id)
    assistant = await fetchone(assistant_)
    config = await ajson_loads(assistant["config"])
    async with get_graph(assistant["graph_id"], config) as graph:
        xray: bool | int = False
        xray_query = request.query_params.get("xray")
        if xray_query:
            if xray_query in ("true", "True"):
                xray = True
            elif xray_query in ("false", "False"):
                xray = False
            else:
                try:
                    xray = int(xray_query)
                except ValueError:
                    raise HTTPException(422, detail="Invalid xray value") from None

                if xray <= 0:
                    raise HTTPException(422, detail="Invalid xray value") from None

        if isinstance(graph, BaseRemotePregel):
            drawable_graph = await graph.fetch_graph(xray=xray)
            return ApiResponse(drawable_graph.to_json())

        try:
            drawable_graph = await graph.aget_graph(xray=xray)
            return ApiResponse(drawable_graph.to_json())
        except NotImplementedError:
            raise HTTPException(
                422, detail="The graph does not support visualization"
            ) from None


@retry_db
async def get_assistant_subgraphs(
    request: ApiRequest,
) -> ApiResponse:
    """Get an assistant by ID."""
    assistant_id = request.path_params["assistant_id"]
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant_ = await Assistants.get(conn, assistant_id)
    assistant = await fetchone(assistant_)
    config = await ajson_loads(assistant["config"])
    async with get_graph(assistant["graph_id"], config) as graph:
        namespace = request.path_params.get("namespace")

        if isinstance(graph, BaseRemotePregel):
            return ApiResponse(
                await graph.fetch_subgraphs(
                    namespace=namespace,
                    recurse=request.query_params.get("recurse", "False")
                    in ("true", "True"),
                )
            )

        try:
            return ApiResponse(
                {
                    ns: _graph_schemas(subgraph)
                    async for ns, subgraph in graph.aget_subgraphs(
                        namespace=namespace,
                        recurse=request.query_params.get("recurse", "False")
                        in ("true", "True"),
                    )
                }
            )
        except NotImplementedError:
            raise HTTPException(
                422, detail="The graph does not support visualization"
            ) from None


@retry_db
async def get_assistant_schemas(
    request: ApiRequest,
) -> ApiResponse:
    """Get an assistant by ID."""
    assistant_id = request.path_params["assistant_id"]
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant_ = await Assistants.get(conn, assistant_id)
    assistant = await fetchone(assistant_)
    config = await ajson_loads(assistant["config"])
    async with get_graph(assistant["graph_id"], config) as graph:
        if isinstance(graph, BaseRemotePregel):
            schemas = await graph.fetch_state_schema()
            return ApiResponse(
                {
                    "graph_id": assistant["graph_id"],
                    "input_schema": schemas.get("input"),
                    "output_schema": schemas.get("output"),
                    "state_schema": schemas.get("state"),
                    "config_schema": schemas.get("config"),
                }
            )

        try:
            input_schema = graph.get_input_jsonschema()
        except Exception as e:
            logger.warning(
                f"Failed to get input schema for graph {graph.name} with error: `{str(e)}`"
            )
            input_schema = None
        try:
            output_schema = graph.get_output_jsonschema()
        except Exception as e:
            logger.warning(
                f"Failed to get output schema for graph {graph.name} with error: `{str(e)}`"
            )
            output_schema = None

        state_schema = _state_jsonschema(graph)
        try:
            config_schema = _get_configurable_jsonschema(graph)
        except Exception as e:
            config_schema = None
            logger.warning(
                f"Failed to get config schema for graph {graph.name} with error: `{str(e)}`"
            )
        return ApiResponse(
            {
                "graph_id": assistant["graph_id"],
                "input_schema": input_schema,
                "output_schema": output_schema,
                "state_schema": state_schema,
                "config_schema": config_schema,
            }
        )


@retry_db
async def patch_assistant(
    request: ApiRequest,
) -> ApiResponse:
    """Update an assistant."""
    assistant_id = request.path_params["assistant_id"]
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    payload = await request.json(AssistantPatch)
    async with connect() as conn:
        assistant = await Assistants.patch(
            conn,
            assistant_id,
            config=payload.get("config"),
            graph_id=payload.get("graph_id"),
            metadata=payload.get("metadata"),
            name=payload.get("name"),
        )
    return ApiResponse(await fetchone(assistant))


@retry_db
async def delete_assistant(request: ApiRequest) -> ApiResponse:
    """Delete an assistant by ID."""
    assistant_id = request.path_params["assistant_id"]
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        aid = await Assistants.delete(conn, assistant_id)
    await fetchone(aid)
    return Response(status_code=204)


@retry_db
async def get_assistant_versions(request: ApiRequest) -> ApiResponse:
    """Get all versions of an assistant."""
    assistant_id = request.path_params["assistant_id"]
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    payload = await request.json(AssistantVersionsSearchRequest)
    async with connect() as conn:
        assistants_iter = await Assistants.get_versions(
            conn,
            assistant_id,
            metadata=payload.get("metadata") or {},
            limit=payload.get("limit") or 10,
            offset=payload.get("offset") or 0,
        )
    assistants = [assistant async for assistant in assistants_iter]
    if not assistants:
        raise HTTPException(
            status_code=404, detail=f"Assistant {assistant_id} not found"
        )
    return ApiResponse(assistants)


@retry_db
async def set_latest_assistant_version(request: ApiRequest) -> ApiResponse:
    """Change the version of an assistant."""
    assistant_id = request.path_params["assistant_id"]
    payload = await request.json(AssistantVersionChange)
    validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    async with connect() as conn:
        assistant = await Assistants.set_latest(
            conn, assistant_id, payload.get("version")
        )
    return ApiResponse(await fetchone(assistant, not_found_code=404))


assistants_routes: list[BaseRoute] = [
    ApiRoute("/assistants", create_assistant, methods=["POST"]),
    ApiRoute("/assistants/search", search_assistants, methods=["POST"]),
    ApiRoute(
        "/assistants/{assistant_id}/latest",
        set_latest_assistant_version,
        methods=["POST"],
    ),
    ApiRoute(
        "/assistants/{assistant_id}/versions", get_assistant_versions, methods=["POST"]
    ),
    ApiRoute("/assistants/{assistant_id}", get_assistant, methods=["GET"]),
    ApiRoute("/assistants/{assistant_id}/graph", get_assistant_graph, methods=["GET"]),
    ApiRoute(
        "/assistants/{assistant_id}/schemas", get_assistant_schemas, methods=["GET"]
    ),
    ApiRoute(
        "/assistants/{assistant_id}/subgraphs", get_assistant_subgraphs, methods=["GET"]
    ),
    ApiRoute(
        "/assistants/{assistant_id}/subgraphs/{namespace}",
        get_assistant_subgraphs,
        methods=["GET"],
    ),
    ApiRoute("/assistants/{assistant_id}", patch_assistant, methods=["PATCH"]),
    ApiRoute("/assistants/{assistant_id}", delete_assistant, methods=["DELETE"]),
]

assistants_routes = [route for route in assistants_routes if route is not None]
