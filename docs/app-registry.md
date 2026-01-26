# AppRegistry - Custom Routes

The `AppRegistry` allows you to register custom FastAPI routers to extend your agent with additional API endpoints.

## Registration

Custom routes must be registered in your agent module (the one specified in `aion.yaml` path) before the server starts.

**Example:**

```yaml
aion:
  agents:
    my_agent:
      path: "./agent.py:chat_agent"
```

**agent.py:**

```python
from fastapi import APIRouter
from aion.server.core.app.registry import app_registry

# Register custom routes in your agent module
custom_router = APIRouter(prefix="/api/custom")

@custom_router.get("/health")
async def health():
    return {"status": "ok"}

app_registry.add_router(custom_router)

# ... your agent code
```

## API Reference

### `add_router(router: APIRouter)`

Register a FastAPI router to extend your agent's endpoints.

- Multiple routers can be registered
- Routers are applied in the order they were registered

### `get_routers()`

Get all currently registered routers.
