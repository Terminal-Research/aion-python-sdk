import asyncio
import importlib
import importlib.util
import os

import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from langgraph_api.api.assistants import assistants_routes
from langgraph_api.api.meta import meta_info, meta_metrics
from langgraph_api.api.openapi import get_openapi_spec
from langgraph_api.api.runs import runs_routes
from langgraph_api.api.store import store_routes
from langgraph_api.api.threads import threads_routes
from langgraph_api.api.ui import ui_routes
from langgraph_api.auth.middleware import auth_middleware
from langgraph_api.config import HTTP_CONFIG, MIGRATIONS_PATH
from langgraph_api.graph import js_bg_tasks
from langgraph_api.validation import DOCS_HTML
from langgraph_storage.database import connect, healthcheck

logger = structlog.stdlib.get_logger(__name__)


async def ok(request: Request):
    check_db = int(request.query_params.get("check_db", "0"))  # must be "0" or "1"
    if check_db:
        await healthcheck()
    if js_bg_tasks:
        from langgraph_api.js.remote import js_healthcheck

        await js_healthcheck()
    return JSONResponse({"ok": True})


async def openapi(request: Request):
    spec = await asyncio.to_thread(get_openapi_spec)
    return Response(spec, media_type="application/json")


async def docs(request: Request):
    return HTMLResponse(DOCS_HTML)


meta_routes: list[BaseRoute] = [
    Route("/ok", ok, methods=["GET"]),
    Route("/openapi.json", openapi, methods=["GET"]),
    Route("/docs", docs, methods=["GET"]),
    Route("/info", meta_info, methods=["GET"]),
    Route("/metrics", meta_metrics, methods=["GET"]),
]

protected_routes: list[BaseRoute] = []

if HTTP_CONFIG:
    if not HTTP_CONFIG.get("disable_assistants"):
        protected_routes.extend(assistants_routes)
    if not HTTP_CONFIG.get("disable_runs"):
        protected_routes.extend(runs_routes)
    if not HTTP_CONFIG.get("disable_threads"):
        protected_routes.extend(threads_routes)
    if not HTTP_CONFIG.get("disable_store"):
        protected_routes.extend(store_routes)
    if not HTTP_CONFIG.get("disable_ui"):
        protected_routes.extend(ui_routes)
else:
    protected_routes.extend(assistants_routes)
    protected_routes.extend(runs_routes)
    protected_routes.extend(threads_routes)
    protected_routes.extend(store_routes)
    protected_routes.extend(ui_routes)

routes: list[BaseRoute] = []
user_router = None


def load_custom_app(app_import: str) -> Starlette | None:
    # Expect a string in either "path/to/file.py:my_variable" or "some.module.in:my_variable"
    logger.info(f"Loading custom app from {app_import}")
    path, name = app_import.rsplit(":", 1)
    try:
        os.environ["__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT"] = "true"
        if os.path.isfile(path) or path.endswith(".py"):
            # Import from file path using a unique module name.
            spec = importlib.util.spec_from_file_location("user_router_module", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load spec from {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            # Import as a normal module.
            module = importlib.import_module(path)
        user_router = getattr(module, name)
        if not isinstance(user_router, Starlette):
            raise TypeError(
                f"Object '{name}' in module '{path}' is not a Starlette or FastAPI application. "
                "Please initialize your app by importing and using the appropriate class: "
                "\nfrom starlette.applications import Starlette\n\napp = Starlette(...)\n\n"
                "or\n\nfrom fastapi import FastAPI\n\napp = FastAPI(...)\n\n"
            )
    except ImportError as e:
        raise ImportError(f"Failed to import app module '{path}'") from e
    except AttributeError as e:
        raise AttributeError(f"App '{name}' not found in module '{path}'") from e
    finally:
        os.environ.pop("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT", None)
    return user_router


if HTTP_CONFIG:
    if router_import := HTTP_CONFIG.get("app"):
        user_router = load_custom_app(router_import)
    if not HTTP_CONFIG.get("disable_meta"):
        routes.extend(meta_routes)
    if protected_routes:
        routes.append(
            Mount(
                "/",
                middleware=[auth_middleware],
                routes=protected_routes,
            ),
        )

else:
    routes.extend(meta_routes)
    routes.append(Mount("/", middleware=[auth_middleware], routes=protected_routes))


if "inmem" in MIGRATIONS_PATH:

    async def truncate(request: Request):
        from langgraph_storage.checkpoint import Checkpointer

        Checkpointer().clear()
        async with connect() as conn:
            conn.clear()
        return JSONResponse({"ok": True})

    routes.insert(0, Route("/internal/truncate", truncate, methods=["POST"]))
