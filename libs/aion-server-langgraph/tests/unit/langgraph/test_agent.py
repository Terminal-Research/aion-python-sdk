import pytest
import tempfile
import yaml
from pathlib import Path
from unittest.mock import Mock, patch
from pydantic import ValidationError

from aion.server.langgraph.agent.base import BaseAgent
from aion.server.langgraph.agent.models import AgentConfig, ConfigurationField, ConfigurationType, AgentSkill
from aion.server.langgraph.agent.config_processor import AgentConfigProcessor
from aion.server.langgraph.agent.manager import AgentManager


class TestAgentConfigValidation:
    """Test critical validation logic that could break the system."""

    def test_invalid_version_format(self):
        """Version validation is critical for compatibility tracking."""
        with pytest.raises(ValidationError, match="Version must be in format X.Y.Z"):
            AgentConfig(path="test", version="1.0")

    def test_duplicate_skill_ids(self):
        """Duplicate skill IDs could cause routing conflicts."""
        skills = [
            AgentSkill(id="skill1", name="Skill 1"),
            AgentSkill(id="skill1", name="Skill 2")  # Duplicate ID
        ]

        with pytest.raises(ValidationError, match="Skill IDs must be unique"):
            AgentConfig(path="test", skills=skills)

    def test_configuration_field_array_validation(self):
        """Array configuration validation is complex and error-prone."""
        # Valid array field
        array_field = ConfigurationField(
            type=ConfigurationType.ARRAY,
            items=ConfigurationField(type=ConfigurationType.STRING)
        )
        assert isinstance(array_field.items, ConfigurationField)

        # Invalid array field - items for non-array type
        with pytest.raises(ValueError, match="Items field is only valid for array and object types"):
            ConfigurationField(
                type=ConfigurationType.STRING,
                items=ConfigurationField(type=ConfigurationType.STRING)
            )


class TestAgentConfigProcessor:
    """Test the most complex and failure-prone component."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def agent_config_processor(self, temp_dir):
        return AgentConfigProcessor(base_dir=temp_dir)

    def test_load_invalid_yaml_file(self, agent_config_processor, temp_dir):
        """YAML parsing errors are common in production."""
        config_file = temp_dir / "invalid.yaml"
        with open(config_file, 'w') as f:
            f.write("invalid: yaml: content: ][")

        with pytest.raises(ValueError, match="Invalid YAML"):
            agent_config_processor.load_config_file(config_file)

    def test_import_nonexistent_module(self, agent_config_processor):
        """Module import failures are critical runtime errors."""
        with pytest.raises(FileNotFoundError):
            agent_config_processor.import_module("nonexistent_module.py")

    def test_discover_agent_item_no_valid_items(self, agent_config_processor, temp_dir):
        """Test failure when module has no valid agent items."""
        # Create module with no BaseAgent subclass or graph
        test_file = temp_dir / "empty_module.py"
        test_file.write_text("""
def regular_function():
    return "not a graph"

class RegularClass:
    pass
""")
        module = agent_config_processor.import_module("empty_module.py")

        with pytest.raises(ValueError, match="No BaseAgent subclass or graph instance found"):
            agent_config_processor.discover_agent_item(module)

    def test_load_from_path_with_invalid_class(self, agent_config_processor, temp_dir):
        """Test loading path that points to non-BaseAgent class."""
        test_file = temp_dir / "invalid_agent.py"
        test_file.write_text("""
class NotAnAgent:
    pass
""")

        with pytest.raises(TypeError, match="Class 'NotAnAgent' must be a subclass of BaseAgent"):
            agent_config_processor.load_from_path("invalid_agent.py:NotAnAgent")

    def test_process_agent_config_missing_path(self, agent_config_processor):
        """Missing path in config should fail gracefully."""
        config = {"name": "Test Agent"}  # Missing required 'path'

        with pytest.raises(ValueError, match="Agent config must specify 'path'"):
            agent_config_processor.process_agent_config("test_agent", config)

    @patch('aion.server.langgraph.agent.config_processor.AgentConfigProcessor.load_from_path')
    def test_process_agent_config_string_path(self, mock_load, agent_config_processor):
        """Test backward compatibility with string paths."""
        mock_agent = Mock(spec=BaseAgent)
        mock_agent.config = None
        mock_load.return_value = mock_agent

        result = agent_config_processor.process_agent_config("test_agent", "test.module:TestAgent")

        mock_load.assert_called_once_with("test.module:TestAgent")
        assert result.agent_id == "test_agent"
        assert result.config is not None  # Should get minimal config


class TestBaseAgentCriticalPaths:
    """Test BaseAgent's most important functionality."""

    def test_config_setter_type_validation(self):
        """Config type validation prevents runtime errors."""
        agent = BaseAgent()

        # Valid config
        config = AgentConfig(path="test")
        agent.config = config
        assert agent.config == config

        # Invalid config type should raise TypeError
        with pytest.raises(TypeError, match="Config must be an AgentConfig instance"):
            agent.config = "invalid_config"

    def test_create_graph_not_implemented_error(self):
        """Ensure proper error when subclass doesn't implement create_graph."""
        agent = BaseAgent()  # No graph_source provided

        with pytest.raises(NotImplementedError, match="Subclasses must implement create_graph"):
            agent.create_graph()

    def test_graph_compilation_failure_handling(self):
        """Test handling when graph compilation fails."""
        # Patch the GraphCheckpointerManager where it's imported in base.py
        with patch('aion.server.langgraph.agent.base.GraphCheckpointerManager') as mock_checkpointer:
            mock_checkpointer_instance = Mock()
            mock_checkpointer_instance.get_checkpointer.side_effect = Exception("Compilation failed")
            mock_checkpointer.return_value = mock_checkpointer_instance

            # Create a mock graph that looks like a real Graph
            mock_graph = Mock()
            mock_graph.__class__.__name__ = "StateGraph"

            agent = BaseAgent(graph_source=mock_graph)

            # Should propagate compilation errors
            with pytest.raises(Exception, match="Compilation failed"):
                agent.create_compiled_graph()


class TestAgentManagerCriticalOperations:
    """Test AgentManager's core functionality that could break the system."""

    @pytest.fixture
    def manager(self):
        # Clear singleton instance for testing
        AgentManager._instances = {}
        return AgentManager()

    def test_set_agent_type_validation(self, manager):
        """Type validation prevents invalid agents from being registered."""
        with pytest.raises(TypeError, match="Agent must be an instance of BaseAgent"):
            manager.set_agent("test", "not_an_agent")

    def test_get_compiled_graph_error_propagation(self, manager):
        """Ensure compilation errors are properly propagated."""
        agent = Mock(spec=BaseAgent)
        agent.get_compiled_graph.side_effect = Exception("Graph compilation failed")

        manager.set_agent("test_agent", agent)

        with pytest.raises(Exception, match="Graph compilation failed"):
            manager.get_compiled_graph("test_agent")

    @patch('aion.server.langgraph.agent.manager.AgentConfigProcessor')
    def test_initialize_agents_file_not_found(self, mock_processor_class, manager):
        """Test graceful handling when config file doesn't exist."""
        mock_processor = Mock()
        mock_processor.load_and_process_config.side_effect = FileNotFoundError("Config file not found")
        mock_processor_class.return_value = mock_processor

        with pytest.raises(FileNotFoundError):
            manager.initialize_agents("nonexistent.yaml")

    @patch('aion.server.langgraph.agent.manager.AgentConfigProcessor')
    def test_initialize_agents_invalid_config(self, mock_processor_class, manager):
        """Test handling of invalid configuration during initialization."""
        mock_processor = Mock()
        mock_processor.load_and_process_config.side_effect = ValueError("Invalid config format")
        mock_processor_class.return_value = mock_processor

        with pytest.raises(ValueError, match="Invalid config format"):
            manager.initialize_agents("invalid.yaml")


class TestIntegrationScenarios:
    """Test critical integration scenarios that could fail in production."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_full_agent_loading_workflow(self, temp_dir):
        """Test complete workflow from config file to working agent."""
        # Create a valid agent module
        agent_file = temp_dir / "test_agent.py"
        agent_file.write_text("""
from aion.server.langgraph.agent.base import BaseAgent
from unittest.mock import Mock

class TestAgent(BaseAgent):
    def create_graph(self):
        # Return a mock graph for testing
        mock_graph = Mock()
        mock_graph.__class__.__name__ = "StateGraph"
        return mock_graph
""")

        # Create valid config file
        config_data = {
            "aion": {
                "agent": {
                    "test_agent": {
                        "path": "test_agent.py:TestAgent",
                        "name": "Test Agent",
                        "description": "A test agent",
                        "version": "1.0.0"
                    }
                }
            }
        }

        config_file = temp_dir / "test_config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Test the full workflow
        processor = AgentConfigProcessor(base_dir=temp_dir)
        agents = processor.load_and_process_config(config_file)

        assert len(agents) == 1
        assert "test_agent" in agents

        agent = agents["test_agent"]
        assert isinstance(agent, BaseAgent)
        assert agent.agent_id == "test_agent"
        assert agent.config.name == "Test Agent"

        # Test that graph creation works
        graph = agent.get_graph()
        assert graph is not None

    def test_agent_manager_integration(self, temp_dir):
        """Test AgentManager with real agent loading."""
        # Clear singleton
        AgentManager._instances = {}
        manager = AgentManager()

        # Create simple agent using a proper graph mock
        agent_file = temp_dir / "simple_agent.py"
        agent_file.write_text("""
from unittest.mock import Mock

def create_simple_graph():
    '''Function that returns a graph instance'''
    mock_graph = Mock()
    mock_graph.__class__.__name__ = "StateGraph"
    return mock_graph

# Use the function as the export
simple_graph = create_simple_graph
""")

        config_data = {
            "aion": {
                "agent": {
                    "simple_agent": "simple_agent.py:simple_graph"
                }
            }
        }

        config_file = temp_dir / "agent_config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Initialize agents through manager
        manager.initialize_agents(config_file)

        assert manager.has_active_agents()
        assert "simple_agent" in manager.list_agents()

        agent = manager.get_agent("simple_agent")
        assert agent is not None
        assert agent.agent_id == "simple_agent"
