from typing import Any

from langgraph_sdk.auth import Auth
from starlette.responses import Response
from starlette.routing import BaseRoute

from langgraph_api.auth.custom import handle_event as _handle_event
from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.utils import get_auth_ctx
from langgraph_api.validation import (
    StoreDeleteRequest,
    StoreListNamespacesRequest,
    StorePutRequest,
    StoreSearchRequest,
)
from langgraph_storage.retry import retry_db
from langgraph_storage.store import Store


def _validate_namespace(namespace: tuple[str, ...]) -> Response | None:
    for label in namespace:
        if not label or "." in label:
            return Response(
                status_code=422,
                content=f"Namespace labels cannot be empty or contain periods. Received: {namespace}",
            )


async def handle_event(
    action: str,
    value: Any,
) -> None:
    ctx = get_auth_ctx()
    if not ctx:
        return
    await _handle_event(
        Auth.types.AuthContext(
            user=ctx.user,
            permissions=ctx.permissions,
            resource="store",
            action=action,
        ),
        value,
    )


@retry_db
async def put_item(request: ApiRequest):
    """Store or update an item."""
    payload = await request.json(StorePutRequest)
    namespace = tuple(payload["namespace"]) if payload.get("namespace") else ()
    if err := _validate_namespace(namespace):
        return err
    handler_payload = {
        "namespace": namespace,
        "key": payload["key"],
        "value": payload["value"],
    }
    await handle_event("put", handler_payload)
    await Store().aput(namespace, handler_payload["key"], handler_payload["value"])
    return Response(status_code=204)


@retry_db
async def get_item(request: ApiRequest):
    """Retrieve a single item."""
    namespace = tuple(request.query_params.get("namespace", "").split("."))
    if err := _validate_namespace(namespace):
        return err
    key = request.query_params.get("key")
    if not key:
        return ApiResponse({"error": "Key is required"}, status_code=400)
    handler_payload = {
        "namespace": namespace,
        "key": key,
    }
    await handle_event("get", handler_payload)
    result = await Store().aget(namespace, key)
    return ApiResponse(result.dict() if result is not None else None)


@retry_db
async def delete_item(request: ApiRequest):
    """Delete an item."""
    payload = await request.json(StoreDeleteRequest)
    namespace = tuple(payload["namespace"]) if payload.get("namespace") else ()
    if err := _validate_namespace(namespace):
        return err
    handler_payload = {
        "namespace": namespace,
        "key": payload["key"],
    }
    await handle_event("delete", handler_payload)
    await Store().adelete(handler_payload["namespace"], handler_payload["key"])
    return Response(status_code=204)


@retry_db
async def search_items(request: ApiRequest):
    """Search for items within a namespace prefix."""
    payload = await request.json(StoreSearchRequest)
    namespace_prefix = tuple(payload["namespace_prefix"])
    if err := _validate_namespace(namespace_prefix):
        return err
    filter = payload.get("filter")
    limit = payload.get("limit") or 10
    offset = payload.get("offset") or 0
    query = payload.get("query")
    handler_payload = {
        "namespace": namespace_prefix,
        "filter": filter,
        "limit": limit,
        "offset": offset,
        "query": query,
    }
    await handle_event("search", handler_payload)
    items = await Store().asearch(
        handler_payload["namespace"],
        filter=handler_payload["filter"],
        limit=handler_payload["limit"],
        offset=handler_payload["offset"],
        query=handler_payload["query"],
    )
    return ApiResponse({"items": [item.dict() for item in items]})


@retry_db
async def list_namespaces(request: ApiRequest):
    """List namespaces with optional match conditions."""
    payload = await request.json(StoreListNamespacesRequest)
    prefix = tuple(payload["prefix"]) if payload.get("prefix") else None
    suffix = tuple(payload["suffix"]) if payload.get("suffix") else None
    if prefix and (err := _validate_namespace(prefix)):
        return err
    if suffix and (err := _validate_namespace(suffix)):
        return err
    max_depth = payload.get("max_depth")
    limit = payload.get("limit", 100)
    offset = payload.get("offset", 0)
    handler_payload = {
        "namespace": prefix,
        "suffix": suffix,
        "max_depth": max_depth,
        "limit": limit,
        "offset": offset,
    }
    await handle_event("list_namespaces", handler_payload)
    result = await Store().alist_namespaces(
        prefix=handler_payload["namespace"],
        suffix=handler_payload["suffix"],
        max_depth=handler_payload["max_depth"],
        limit=handler_payload["limit"],
        offset=handler_payload["offset"],
    )
    return ApiResponse({"namespaces": result})


store_routes: list[BaseRoute] = [
    ApiRoute("/store/items", endpoint=put_item, methods=["PUT"]),
    ApiRoute("/store/items", endpoint=get_item, methods=["GET"]),
    ApiRoute("/store/items", endpoint=delete_item, methods=["DELETE"]),
    ApiRoute("/store/items/search", endpoint=search_items, methods=["POST"]),
    ApiRoute("/store/namespaces", endpoint=list_namespaces, methods=["POST"]),
]
