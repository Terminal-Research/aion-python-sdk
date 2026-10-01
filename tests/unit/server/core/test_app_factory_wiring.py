"""How ``AppFactory`` wires the request path: the task store, push configs, token verification and middlewares.

What building the request handler would construct around them is replaced
with inert stand-ins, so each test sees only the call or the order it asserts.
"""

from unittest.mock import AsyncMock, Mock

import pytest
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

    create_push.assert_called_once_with(
        factory.db_factory.db_manager, owner_resolver=agent.owner_resolver
    )


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
    assert factory.fastapi_app.user_middleware[0].kwargs == {"verifier": verifier}


async def test_wrong_authentication_settings_stop_startup_before_anything_else(monkeypatch) -> None:
    """Raised, not turned into a quiet ``None``: a server must not start unprotected."""
    factory, _, _ = _factory()
    monkeypatch.setattr(
        factory_module, "build_token_verifier", Mock(side_effect=AuthConfigurationError("DEPLOYMENT_ID"))
    )

    with pytest.raises(AuthConfigurationError):
        await factory.initialize()

    factory.db_factory.initialize.assert_not_called()
