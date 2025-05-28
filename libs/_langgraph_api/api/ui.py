import json
import os
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from orjson import loads
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount
from starlette.staticfiles import StaticFiles

from langgraph_api.route import ApiRequest, ApiRoute

# Get path to built UI assets
UI_DIR = Path(os.path.dirname(__file__)).parent / "js" / "ui"
SCHEMAS_FILE = Path(os.path.dirname(__file__)).parent / "js" / "client.ui.schemas.json"


class UiSchema(TypedDict):
    name: str
    assets: list[str]


@lru_cache(maxsize=1)
def load_ui_schemas() -> dict[str, UiSchema]:
    """Load and cache UI schema mappings from JSON file."""
    if not SCHEMAS_FILE.exists():
        return {}

    with open(SCHEMAS_FILE) as f:
        return loads(f.read())


async def handle_ui(request: ApiRequest) -> Response:
    """Serve UI HTML with appropriate script/style tags."""
    graph_id = request.path_params["graph_id"]
    host = request.headers.get("host")
    message = await request.json(schema=None)

    # Load UI file paths from schema
    schemas = load_ui_schemas()

    if graph_id not in schemas:
        return Response(f"UI not found for graph '{graph_id}'", status_code=404)

    result = []
    for filepath in schemas[graph_id]["assets"]:
        basename = os.path.basename(filepath)
        ext = os.path.splitext(basename)[1]

        if ext == ".css":
            result.append(
                f'<link rel="stylesheet" href="//{host}/ui/{graph_id}/{basename}" />'
            )
        elif ext == ".js":
            result.append(
                f'<script src="//{host}/ui/{graph_id}/{basename}" '
                f'onload=\'__LGUI_{graph_id}.render({json.dumps(message["name"])}, "{{{{shadowRootId}}}}")\'>'
                '</script>'
            )

    return Response(content="\n".join(result), headers={"Content-Type": "text/html"})


ui_routes: list[BaseRoute] = [
    ApiRoute("/ui/{graph_id}", handle_ui, methods=["POST"]),
    Mount("/ui", StaticFiles(directory=UI_DIR, check_dir=False)),
]
