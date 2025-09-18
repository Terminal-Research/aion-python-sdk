from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCard

from aion.server.core.app import AppFactory
from aion.server.langgraph.agent import BaseAgent


class TestAppFactory:
    """Test cases for AppFactory critical functionality."""

    def test_init_with_config_and_context(self, mock_agent_config, mock_app_context):
        """Test initialization with agent config and context."""
        factory = AppFactory(
            agent_id="test_agent",
            agent_config=mock_agent_config,
            context=mock_app_context,
            base_path=Path("/test/path")
        )

        assert factory.agent_id == "test_agent"
        assert factory.agent_config == mock_agent_config
        assert factory.context == mock_app_context
        assert factory.base_path == Path("/test/path")
        assert factory.a2a_app is None
        assert factory.starlette_app is None
        assert factory.agent is None

    @pytest.mark.asyncio
    async def test_initialize_success(self, mock_agent_config, mock_app_context):
        """Test successful factory initialization."""
        factory = AppFactory(
            agent_id="test_agent",
            agent_config=mock_agent_config,
            context=mock_app_context
        )

        with patch.object(factory, '_initialize', new_callable=AsyncMock) as mock_initialize:
            result = await factory.initialize()

            assert result == factory
            mock_app_context.app_settings.set_agent_config.assert_called_once_with("test_agent", mock_agent_config)
            mock_initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_failure_cleanup(self, mock_agent_config, mock_app_context):
        """Test factory initialization failure triggers cleanup."""
        factory = AppFactory(
            agent_id="test_agent",
            agent_config=mock_agent_config,
            context=mock_app_context
        )

        with patch.object(factory, '_initialize', side_effect=Exception("Init failed")) as mock_initialize, \
                patch.object(factory, 'shutdown', new_callable=AsyncMock) as mock_shutdown:
            result = await factory.initialize()

            assert result is None
            mock_initialize.assert_called_once()
            mock_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_a2a_app_already_created_raises_error(self, factory_with_agent):
        """Test creating A2A app when already created raises error."""
        factory_with_agent.a2a_app = MagicMock()  # Simulate already created app

        with pytest.raises(RuntimeError, match="A2A application is already created"):
            await factory_with_agent._create_a2a_app()

    @pytest.mark.asyncio
    async def test_create_a2a_app_no_agent_raises_error(self, factory_without_agent):
        """Test creating A2A app without agent raises error."""
        factory_without_agent.agent = None

        with pytest.raises(RuntimeError, match="Agent must be initialized before creating A2A app"):
            await factory_without_agent._create_a2a_app()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AionA2AStarletteApplication')
    async def test_create_a2a_app_success(self, mock_a2a_app_class, factory_with_agent):
        """Test successful A2A app creation."""
        mock_a2a_app = MagicMock()
        mock_a2a_app_class.return_value = mock_a2a_app

        with patch.object(factory_with_agent, '_create_agent_card') as mock_create_card, \
                patch.object(factory_with_agent, '_create_request_handler',
                             new_callable=AsyncMock) as mock_create_handler:
            mock_agent_card = MagicMock()
            mock_request_handler = MagicMock()
            mock_create_card.return_value = mock_agent_card
            mock_create_handler.return_value = mock_request_handler

            await factory_with_agent._create_a2a_app()

            assert factory_with_agent.a2a_app == mock_a2a_app
            mock_create_card.assert_called_once()
            mock_create_handler.assert_called_once()

            mock_a2a_app_class.assert_called_once_with(
                agent_card=mock_agent_card,
                http_handler=mock_request_handler
            )

    def test_create_agent_card_no_agent_raises_error(self, factory_without_agent):
        """Test creating agent card without agent raises error."""
        factory_without_agent.agent = None

        with pytest.raises(RuntimeError, match="No agent available to create agent card"):
            factory_without_agent._create_agent_card()

    def test_create_agent_card_success(self, factory_with_agent):
        """Test successful agent card creation."""
        mock_agent_card = MagicMock(spec=AgentCard)
        factory_with_agent.agent.card = mock_agent_card

        result = factory_with_agent._create_agent_card()

        assert result == mock_agent_card

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.InMemoryPushNotificationConfigStore')
    @patch('aion.server.core.app.factory.factory.AionRequestHandler')
    @patch('aion.server.core.app.factory.factory.LanggraphAgentExecutor')
    async def test_create_request_handler_success(self, mock_executor_class, mock_handler_class,
                                                  mock_push_store_class, factory_with_agent):
        """Test successful request handler creation."""
        mock_compiled_graph = MagicMock()
        factory_with_agent.agent.get_compiled_graph.return_value = mock_compiled_graph

        mock_executor = MagicMock()
        mock_handler = MagicMock()
        mock_push_store = MagicMock()
        mock_task_store = MagicMock()

        mock_executor_class.return_value = mock_executor
        mock_handler_class.return_value = mock_handler
        mock_push_store_class.return_value = mock_push_store
        factory_with_agent.context.store_manager.get_store.return_value = mock_task_store

        result = await factory_with_agent._create_request_handler()

        assert result == mock_handler
        factory_with_agent.context.store_manager.initialize.assert_called_once()
        mock_executor_class.assert_called_once_with(mock_compiled_graph)
        mock_handler_class.assert_called_once_with(
            agent_executor=mock_executor,
            task_store=mock_task_store,
            push_config_store=mock_push_store
        )

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AgentManager')
    async def test_init_agent_success(self, mock_agent_manager_class, factory_without_agent):
        """Test successful agent initialization."""
        mock_agent_manager = MagicMock()
        mock_agent = MagicMock(spec=BaseAgent)
        mock_agent_manager.create_agent.return_value = mock_agent
        mock_agent_manager.precompile_agent.return_value = True
        mock_agent_manager_class.return_value = mock_agent_manager

        await factory_without_agent._init_agent()

        assert factory_without_agent.agent == mock_agent
        assert factory_without_agent.agent_manager == mock_agent_manager
        mock_agent_manager_class.assert_called_once_with(base_path=factory_without_agent.base_path)
        mock_agent_manager.create_agent.assert_called_once_with("test_agent", factory_without_agent.agent_config)
        mock_agent_manager.precompile_agent.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AgentManager')
    async def test_init_agent_precompile_fails(self, mock_agent_manager_class, factory_without_agent):
        """Test agent initialization fails when precompile fails."""
        mock_agent_manager = MagicMock()
        mock_agent = MagicMock(spec=BaseAgent)
        mock_agent_manager.create_agent.return_value = mock_agent
        mock_agent_manager.precompile_agent.return_value = False
        mock_agent_manager_class.return_value = mock_agent_manager

        with pytest.raises(RuntimeError, match="Failed to pre-compile agent 'test_agent'"):
            await factory_without_agent._init_agent()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    async def test_init_db_no_pg_url(self, mock_db_settings, factory_without_agent):
        """Test database initialization skips when no PostgreSQL URL."""
        mock_db_settings.pg_url = None

        await factory_without_agent._init_db()
        # Should complete without error and log info message

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    async def test_init_db_connection_verification_fails(self, mock_verify_connection, mock_db_settings,
                                                         factory_without_agent):
        """Test database initialization handles connection verification failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = False

        await factory_without_agent._init_db()

        mock_verify_connection.assert_called_once_with("postgresql://test")

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    async def test_init_db_manager_initialization_fails(self, mock_verify_connection, mock_db_settings,
                                                        factory_without_agent):
        """Test database initialization handles db manager initialization failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        factory_without_agent.context.db_manager.initialize = AsyncMock(side_effect=Exception("DB init failed"))

        with patch.object(factory_without_agent, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory_without_agent._init_db()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    @patch('aion.server.core.app.factory.factory.upgrade_to_head')
    async def test_init_db_migration_fails(self, mock_upgrade, mock_verify_connection, mock_db_settings,
                                           factory_without_agent):
        """Test database initialization handles migration failure."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        factory_without_agent.context.db_manager.initialize = AsyncMock()
        mock_upgrade.side_effect = Exception("Migration failed")

        with patch.object(factory_without_agent, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory_without_agent._init_db()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AppLifespan')
    async def test_build_starlette_app_no_a2a_app_raises_error(self, mock_lifespan_class, factory_without_agent):
        """Test building Starlette app without A2A app raises error."""
        factory_without_agent.a2a_app = None

        with pytest.raises(RuntimeError, match="A2A application must be created before building Starlette app"):
            await factory_without_agent._build_starlette_app()

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.AppLifespan')
    async def test_build_starlette_app_success(self, mock_lifespan_class, factory_without_agent):
        """Test successful Starlette app building."""
        mock_a2a_app = MagicMock()
        mock_starlette_app = MagicMock()
        mock_lifespan = MagicMock()
        mock_lifespan.executor = MagicMock()

        factory_without_agent.a2a_app = mock_a2a_app
        mock_a2a_app.build.return_value = mock_starlette_app
        mock_lifespan_class.return_value = mock_lifespan

        await factory_without_agent._build_starlette_app()

        assert factory_without_agent.starlette_app == mock_starlette_app
        mock_lifespan_class.assert_called_once_with(app_factory=factory_without_agent)
        mock_a2a_app.build.assert_called_once_with(lifespan=mock_lifespan.executor)

    @pytest.mark.asyncio
    @patch('aion.server.core.app.factory.factory.db_settings')
    @patch('aion.server.core.app.factory.factory.verify_connection')
    @patch('aion.server.core.app.factory.factory.upgrade_to_head')
    async def test_init_db_success(self, mock_upgrade, mock_verify_connection, mock_db_settings, factory_without_agent):
        """Test successful database initialization."""
        mock_db_settings.pg_url = "postgresql://test"
        mock_verify_connection.return_value = True
        factory_without_agent.context.db_manager.initialize = AsyncMock()
        mock_upgrade.return_value = None

        await factory_without_agent._init_db()

        mock_verify_connection.assert_called_once_with("postgresql://test")
        factory_without_agent.context.db_manager.initialize.assert_called_once_with("postgresql://test")
        mock_upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_calls_cleanup(self, factory_without_agent):
        """Test shutdown calls database cleanup."""
        with patch.object(factory_without_agent, '_cleanup_db', new_callable=AsyncMock) as mock_cleanup:
            await factory_without_agent.shutdown()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_db_not_initialized(self, factory_without_agent):
        """Test database cleanup when not initialized."""
        factory_without_agent.context.db_manager.is_initialized = False

        await factory_without_agent._cleanup_db()

        factory_without_agent.context.db_manager.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_db_handles_close_error(self, factory_without_agent):
        """Test database cleanup handles close errors gracefully."""
        factory_without_agent.context.db_manager.is_initialized = True
        factory_without_agent.context.db_manager.close = AsyncMock(side_effect=Exception("Close failed"))

        # Should not raise exception
        await factory_without_agent._cleanup_db()

        factory_without_agent.context.db_manager.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_db_success(self, factory_without_agent):
        """Test successful database cleanup."""
        factory_without_agent.context.db_manager.is_initialized = True
        factory_without_agent.context.db_manager.close = AsyncMock()

        await factory_without_agent._cleanup_db()

        factory_without_agent.context.db_manager.close.assert_called_once()

    def test_properties(self, factory_with_agent):
        """Test factory properties."""
        # Test is_initialized when not initialized initially
        factory_with_agent.agent = None
        factory_with_agent.a2a_app = None
        factory_with_agent.starlette_app = None
        assert not factory_with_agent.is_initialized

        # Set up components
        factory_with_agent.agent = MagicMock()
        factory_with_agent.a2a_app = MagicMock()
        factory_with_agent.starlette_app = MagicMock()

        # Test is_initialized when initialized
        assert factory_with_agent.is_initialized

        # Test getters
        assert factory_with_agent.get_agent() == factory_with_agent.agent
        assert factory_with_agent.get_starlette_app() == factory_with_agent.starlette_app
        assert factory_with_agent.get_agent_config() == factory_with_agent.agent_config
        assert factory_with_agent.get_agent_host() == "0.0.0.0"
