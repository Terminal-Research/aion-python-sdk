"""How ``AppFactory`` wires the request path: the task store, push configs, token verification and middlewares.

What building the request handler would construct around them is replaced
with inert stand-ins, so each test sees only the call or the order it asserts.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from a2a.utils.push_url_validator import validate_push_notification_url
from fastapi import FastAPI

import aion.server.core.app.factory as factory_module
from aion.server.auth import AuthConfigurationError
from aion.server.core.app.factory import AppFactory
from aion.server.core.middlewares import (
    AionAuthMiddleware,
    AionContextMiddleware,
    TracingMiddleware,
)


@pytest.fixture
def create_push(monkeypatch) -> Mock:
    """Stands in for everything the request handler is built from; returns the push factory's ``create``."""
    monkeypatch.setattr(
        factory_module.AionAgentRequestExecutor, "create", AsyncMock(return_value=Mock())
    )
    create = Mock(return_value=(Mock(), Mock()))
    monkeypatch.setattr(factory_module.PushNotificationFactory, "create", create)
    monkeypatch.setattr(factory_module, "AionRequestHandler", Mock())
    monkeypatch.setattr(factory_module, "AionRequestContextBuilder", Mock())
    monkeypatch.setattr(factory_module, "FilePartPreprocessor", Mock())
    monkeypatch.setattr(
        factory_module.FileUploadManager, "from_settings", classmethod(lambda cls: None)
    )
    return create


def _factory(*, store_resolver=None, upload_manager=None) -> tuple[AppFactory, Mock, Mock]:
    """A factory, its agent and its store manager.

    The store resolves owners with the agent's resolver unless
    ``store_resolver`` names another.
    """
    plugin_factory = Mock()
    plugin_factory.is_initialized.return_value = False
    db_factory = Mock()
    db_factory.is_initialized = False
    agent = Mock()
    agent.id = "agent-1"
    store_manager = Mock()
    store_manager.get_store.return_value.owner_resolver = (
        store_resolver if store_resolver is not None else agent.owner_resolver
    )
    factory = AppFactory(
        aion_agent=agent,
        db_factory=db_factory,
        agent_factory=Mock(),
        plugin_factory=plugin_factory,
        store_manager=store_manager,
        upload_manager=upload_manager,
    )
    return factory, agent, store_manager


@pytest.mark.parametrize("installed", [True, False])
async def test_the_store_is_guarded_exactly_when_a_manager_is_installed(create_push, installed):
    """The guard follows the manager the factory holds, not the setting.

    ``upload_manager`` can be injected with ``FILE_STORAGE_BACKEND`` unset;
    that deployment converts files and must keep its last line of defence.
    Conversely no manager means passthrough, and the store must not strip.
    """
    factory, agent, store_manager = _factory(upload_manager=Mock() if installed else None)

    await factory._create_request_handler()

    store_manager.initialize.assert_called_once_with(
        agent_id="agent-1",
        guard_inline_files=installed,
        owner_resolver=agent.owner_resolver,
    )


async def test_a_store_resolving_owners_differently_stops_startup(create_push) -> None:
    """The tasks and the agent's framework state must name one owner per request.

    A store built earlier with another resolver would file a task under one
    owner and keep its LangGraph/ADK state under another; startup refuses
    rather than serve that.
    """
    factory, _, _ = _factory(store_resolver=lambda context: "someone-else")

    with pytest.raises(RuntimeError, match="resolve owners differently"):
        await factory._create_request_handler()


async def test_push_configs_resolve_owners_like_the_tasks(create_push) -> None:
    """The push config store gets the agent's own resolver."""
    factory, agent, _ = _factory()

    await factory._create_request_handler()

    create_push.assert_called_once()
    assert create_push.call_args.args == (factory.db_factory.db_manager,)
    assert create_push.call_args.kwargs["owner_resolver"] is agent.owner_resolver


@pytest.mark.parametrize(
    "deployment_id, validator",
    [("3f2b8c1e-7d4a-4e5f-9a6b-1c2d3e4f5a6b", validate_push_notification_url), (None, None)],
    ids=["hosted", "not-hosted"],
)
async def test_push_urls_are_checked_on_a_hosted_server_only(create_push, monkeypatch, deployment_id, validator) -> None:
    """The handler, which accepts configs, and the sender, which delivers, get one policy."""
    if deployment_id is None:
        monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    else:
        monkeypatch.setenv("DEPLOYMENT_ID", deployment_id)
    factory, _, _ = _factory()

    await factory._create_request_handler()

    assert create_push.call_args.kwargs["push_url_validator"] is validator
    assert factory_module.AionRequestHandler.call_args.kwargs["push_url_validator"] is validator


@pytest.mark.parametrize("verifier", [Mock(), None], ids=["platform", "local-mode"])
def test_the_caller_is_named_before_anything_reads_the_request(create_push, verifier) -> None:
    """Outermost first, in both modes: the caller, then the execution scope, then the span that reads it."""
    factory, _, _ = _factory()
    factory.fastapi_app = FastAPI()
    factory.token_verifier = verifier

    factory._add_extra_middlewares()

    assert [entry.cls for entry in factory.fastapi_app.user_middleware] == [
        AionAuthMiddleware,
        AionContextMiddleware,
        TracingMiddleware,
    ]
    assert factory.fastapi_app.user_middleware[0].kwargs == {
        "verifier": verifier,
        "application_routes": factory._application_routes,
    }


async def test_wrong_authentication_settings_stop_startup_before_anything_else(monkeypatch) -> None:
    """Raised, not turned into a quiet ``None``: a server must not start unprotected."""
    factory, _, _ = _factory()
    monkeypatch.setattr(
        factory_module, "build_token_verifier", Mock(side_effect=AuthConfigurationError("DEPLOYMENT_ID"))
    )

    with pytest.raises(AuthConfigurationError):
        await factory.initialize()

    factory.db_factory.initialize.assert_not_called()


@pytest.fixture
def registry():
    """The process-wide ``app_registry``, empty before and after the test."""
    from aion.server import app_registry

    app_registry.clear()
    yield app_registry
    app_registry.clear()


async def test_the_registered_routes_are_the_applications_and_the_rest_stays_closed(
    monkeypatch, registry
) -> None:
    """Built in ``_initialize``'s order: the server's routes and middlewares, then the ``AppRegistry`` routers."""
    import httpx
    from fastapi import APIRouter

    factory, _, _ = _factory()
    factory.token_verifier = Mock(load=AsyncMock(), trust_application_users=False)
    for step in (factory.db_factory, factory.agent_factory, factory.plugin_factory):
        step.initialize = AsyncMock()
    factory.db_factory.initialize.return_value = False  # no POSTGRES_URL
    factory.agent_factory.build = AsyncMock()
    factory.plugin_factory.configure_app = AsyncMock()

    async def build_app() -> None:
        factory.fastapi_app = FastAPI()
        factory.fastapi_app.add_api_route("/", lambda: "agent", methods=["POST"])
        factory._add_extra_middlewares()

    monkeypatch.setattr(factory, "_build_app", build_app)
    custom = APIRouter(prefix="/api/custom")
    custom.add_api_route("/health", lambda: "custom", methods=["GET"])
    clashing = APIRouter()
    clashing.add_api_route("/", lambda: "taken", methods=["POST"])
    registry.add_router(custom)
    registry.add_router(clashing)

    await factory._initialize()

    transport = httpx.ASGITransport(app=factory.fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/custom/health")).status_code == 200
        rpc = await client.post("/")
        assert rpc.status_code == 401
        assert rpc.json()["error"]["code"] == -32051
        context_route = await client.post("/context:get")
        assert context_route.status_code == 401
        assert context_route.headers["content-type"] == "application/problem+json"
        schema = await client.get("/openapi.json")
        assert schema.status_code == 401
        assert schema.json()["error"] == "unauthorized"
