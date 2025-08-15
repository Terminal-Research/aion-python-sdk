import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aion.server.core.app import AppConfig, AppFactory
from aion.server.langgraph.agent import BaseAgent
from a2a.types import AgentCard


class TestAppFactory:
    """Test cases for AppFactory critical functionality."""

    def test_init_with_invalid_config_raises_error(self):
        """Test initialization fails with invalid config type."""
        with pytest.raises(TypeError, match="config must be an instance of AppConfig"):
            AppFactory("invalid_config")

    def test_init_with_none_config_uses_default(self):
        """Test initialization with None config creates default AppConfig."""
        factory = AppFactory(None)
        assert isinstance(factory.config, AppConfig)
        assert factory.a2a_app is None
        assert factory.starlette_app is None
        assert factory.agent is None

    @pytest.mark.asyncio
    async def test_initialize_success(self):
        """Test successful factory initialization."""
        config = AppConfig()

        with patch.object(AppFactory, 'create_a2a_app', new_callable=AsyncMock) as mock_create_a2a, \
                patch.object(AppFactory, 'build_starlette_app', new_callable=AsyncMock) as mock_build_starlette:
            factory = await AppFactory.initialize(config)

            assert factory is not None
            assert factory.config == config
            mock_create_a2a.assert_called_once()
            mock_build_starlette.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_failure_cleanup(self):
        """Test factory initialization failure triggers cleanup."""
        config = AppConfig()

        with patch.object(AppFactory, 'create_a2a_app', side_effect=Exception("Init failed")) as mock_create_a2a, \
                patch.object(AppFactory, 'shutdown', new_callable=AsyncMock) as mock_shutdown:
            factory = await AppFactory.initialize(config)

            assert factory is None
            mock_create_a2a.assert_called_once()
            mock_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_a2a_app_already_created_raises_error(self):
        """Test creating A2A app when already created raises error."""
        factory = AppFactory(AppConfig())
        factory.a2a_app = MagicMock()  # Simulate already created app

        with pytest.raises(RuntimeError, match="This app is already created"):
            await factory.create_a2a_app()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AionA2AStarletteApplication')
    async def test_create_a2a_app_success(self, mock_a2a_app_class):
        """Test successful A2A app creation."""
        factory = AppFactory(AppConfig())
        mock_agent = MagicMock(spec=BaseAgent)
        factory.agent = mock_agent

        mock_a2a_app = MagicMock()
        mock_a2a_app_class.return_value = mock_a2a_app

        with patch.object(factory, '_init_db', new_callable=AsyncMock) as mock_init_db, \
                patch.object(factory, '_init_agents', new_callable=AsyncMock) as mock_init_agents, \
                patch.object(factory, '_create_agent_card') as mock_create_card, \
                patch.object(factory, '_create_request_handler', new_callable=AsyncMock) as mock_create_handler:
            mock_agent_card = MagicMock()
            mock_request_handler = MagicMock()
            mock_create_card.return_value = mock_agent_card
            mock_create_handler.return_value = mock_request_handler

            result = await factory.create_a2a_app()

            assert result == mock_a2a_app
            assert factory.a2a_app == mock_a2a_app
            mock_init_db.assert_called_once()
            mock_init_agents.assert_called_once()
            mock_create_card.assert_called_once()
            mock_create_handler.assert_called_once()

            mock_a2a_app_class.assert_called_once_with(
                agent_card=mock_agent_card,
                http_handler=mock_request_handler
            )

    def test_create_agent_card_no_agent_raises_error(self):
        """Test creating agent card without agent raises error."""
        factory = AppFactory(AppConfig())
        factory.agent = None

        with pytest.raises(RuntimeError, match="No agent available to create agent card"):
            factory._create_agent_card()

    def test_create_agent_card_success(self):
        """Test successful agent card creation."""
        config = AppConfig()
        config.host = "localhost"
        config.port = 8000

        factory = AppFactory(config)
        mock_agent = MagicMock(spec=BaseAgent)
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent.get_agent_card.return_value = mock_agent_card
        factory.agent = mock_agent

        result = factory._create_agent_card()

        assert result == mock_agent_card
        mock_agent.get_agent_card.assert_called_once_with("http://localhost:8000/")

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.store_manager')
    @patch('aion.server.core.app.factory.factory.InMemoryPushNotificationConfigStore')
    @patch('aion.server.core.app.factory.factory.AionRequestHandler')
    @patch('aion.server.core.app.factory.factory.LanggraphAgentExecutor')
    async def test_create_request_handler_success(self, mock_executor_class, mock_handler_class,
                                                  mock_push_store_class, mock_store_manager):
        """Test successful request handler creation."""
        factory = AppFactory(AppConfig())
        mock_agent = MagicMock(spec=BaseAgent)
        mock_compiled_graph = MagicMock()
        mock_agent.get_compiled_graph.return_value = mock_compiled_graph
        factory.agent = mock_agent

        mock_executor = MagicMock()
        mock_handler = MagicMock()
        mock_push_store = MagicMock()
        mock_task_store = MagicMock()

        mock_executor_class.return_value = mock_executor
        mock_handler_class.return_value = mock_handler
        mock_push_store_class.return_value = mock_push_store
        mock_store_manager.get_store.return_value = mock_task_store

        result = await factory._create_request_handler()

        assert result == mock_handler
        mock_store_manager.initialize.assert_called_once()
        mock_executor_class.assert_called_once_with(mock_compiled_graph)
        mock_handler_class.assert_called_once_with(
            agent_executor=mock_executor,
            task_store=mock_task_store,
            push_config_store=mock_push_store
        )

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.agent_manager')
    async def test_init_agents_no_agents_raises_error(self, mock_agent_manager):
        """Test agent initialization fails when no agents available."""
        factory = AppFactory(AppConfig())
        mock_agent_manager.has_active_agents.return_value = False

        with pytest.raises(RuntimeError, match="Agent manager has no agents after initialization"):
            await factory._init_agents()

        mock_agent_manager.initialize_agents.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.agent_manager')
    async def test_init_agents_no_first_agent_raises_error(self, mock_agent_manager):
        """Test agent initialization fails when no first agent available."""
        factory = AppFactory(AppConfig())
        mock_agent_manager.has_active_agents.return_value = True
        mock_agent_manager.get_first_agent.return_value = None

        with pytest.raises(RuntimeError, match="No agent available after initialization"):
            await factory._init_agents()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.agent_manager')
    async def test_init_agents_success(self, mock_agent_manager):
        """Test successful agent initialization."""
        factory = AppFactory(AppConfig())
        mock_agent = MagicMock(spec=BaseAgent)
        mock_agent_manager.has_active_agents.return_value = True
        mock_agent_manager.get_first_agent.return_value = mock_agent
        mock_agent_manager.agents = {"test_agent": mock_agent}

        await factory._init_agents()

        assert factory.agent == mock_agent
        mock_agent_manager.initialize_agents.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    async def test_init_db_no_pg_url(self, mock_db_settings):
        """Test database initialization skips when no PostgreSQL URL."""
        mock_db_settings.pg_url = None
        factory = AppFactory(AppConfig())

        await factory._init_db()
        # Should complete without error and log info message

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    async def test_init_db_connection_verification_fails(self, mock_verify_connection, mock_db_settings):
        """Test database initialization handles connection verification failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = False
        factory = AppFactory(AppConfig())

        await factory._init_db()

        mock_verify_connection.assert_called_once_with("postgresql://test")

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.db_manager')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    async def test_init_db_manager_initialization_fails(
            self,
            mock_verify_connection,
            mock_db_manager,
            mock_db_settings
    ):
        """Test database initialization handles db manager initialization failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        mock_db_manager.initialize = AsyncMock(side_effect=Exception("DB init failed"))

        factory = AppFactory(AppConfig())

        with patch.object(factory, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory._init_db()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.db_manager')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    @patch('aion.server.core.app.factory.factory.upgrade_to_head')
    async def test_init_db_migration_fails(
            self,
            mock_upgrade,
            mock_verify_connection,
            mock_db_manager,
            mock_db_settings
    ):
        """Test database initialization handles migration failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        mock_db_manager.initialize = AsyncMock()  # Успешная инициализация
        mock_upgrade.side_effect = Exception("Migration failed")

        factory = AppFactory(AppConfig())

        with patch.object(factory, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory._init_db()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AppLifespan')
    async def test_build_starlette_app_success(self, mock_lifespan_class):
        """Test successful Starlette app building."""
        factory = AppFactory(AppConfig())
        mock_a2a_app = MagicMock()
        mock_starlette_app = MagicMock()
        mock_lifespan = MagicMock()
        mock_lifespan.executor = MagicMock()

        factory.a2a_app = mock_a2a_app
        mock_a2a_app.build.return_value = mock_starlette_app
        mock_lifespan_class.return_value = mock_lifespan

        result = await factory.build_starlette_app()

        assert result == mock_starlette_app
        assert factory.starlette_app == mock_starlette_app
        mock_lifespan_class.assert_called_once_with(app_factory=factory)
        mock_a2a_app.build.assert_called_once_with(lifespan=mock_lifespan.executor)

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.db_manager')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    @patch('aion.server.core.app.factory.factory.upgrade_to_head')
    async def test_init_db_success(
            self,
            mock_upgrade,
            mock_verify_connection,
            mock_db_manager,
            mock_db_settings
    ):
        """Test successful database initialization."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        mock_db_manager.initialize = AsyncMock()
        mock_upgrade.return_value = None

        factory = AppFactory(AppConfig())
        await factory._init_db()

        mock_verify_connection.assert_called_once_with("postgresql://test")
        mock_db_manager.initialize.assert_called_once_with("postgresql://test")
        mock_upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_calls_cleanup(self):
        """Test shutdown calls database cleanup."""
        factory = AppFactory(AppConfig())

        with patch.object(factory, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory.shutdown()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_manager')
    async def test_cleanup_db_not_initialized(self, mock_db_manager):
        """Test database cleanup when not initialized."""
        mock_db_manager.is_initialized = False
        factory = AppFactory(AppConfig())

        await factory._cleanup_db()

        mock_db_manager.close.assert_not_called()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_manager')
    async def test_cleanup_db_handles_close_error(self, mock_db_manager):
        """Test database cleanup handles close errors gracefully."""
        mock_db_manager.is_initialized = True
        mock_db_manager.close = AsyncMock(side_effect=Exception("Close failed"))
        factory = AppFactory(AppConfig())

        # Should not raise exception
        await factory._cleanup_db()

        mock_db_manager.close.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.db_manager')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    @patch('aion.server.core.app.factory.factory.upgrade_to_head')
    async def test_init_db_success(self, mock_upgrade, mock_verify_connection, mock_db_manager, mock_db_settings):
        """Test successful database initialization."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        mock_db_manager.initialize = AsyncMock()
        mock_upgrade.return_value = None

        factory = AppFactory(AppConfig())
        await factory._init_db()

        mock_verify_connection.assert_called_once_with("postgresql://test")
        mock_db_manager.initialize.assert_called_once_with("postgresql://test")
        mock_upgrade.assert_called_once()
        """Test successful database cleanup."""
        mock_db_manager.is_initialized = True
        mock_db_manager.close = AsyncMock()
        factory = AppFactory(AppConfig())

        await factory._cleanup_db()

        mock_db_manager.close.assert_called_once()
