import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")

from aion.server.langgraph.a2a.agent import LanggraphAgent
from aion.server.langgraph.graph import graph_manager


def test_langgraph_agent_uses_first_registered_graph(tmp_path: Path) -> None:
    module_path = tmp_path / "mod.py"
    module_path.write_text(
        """
class DummyGraph:
    pass

def create() -> DummyGraph:
    return DummyGraph()
"""
    )
    config = (
        "aion:\n"
        "  graph:\n"
        "    g1: \"./mod.py:create\"\n"
    )
    cfg_path = tmp_path / "aion.yaml"
    cfg_path.write_text(config)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        graph_manager.initialize_graphs()
        test_graph = graph_manager.get_graph("g1")
        agent = LanggraphAgent(graph=test_graph)
        assert agent.graph is test_graph
    finally:
        os.chdir(cwd)


def test_langgraph_agent_no_graphs(tmp_path: Path) -> None:
    cfg_path = tmp_path / "aion.yaml"
    cfg_path.write_text("aion:\n  graph: {}\n")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        graph_manager.initialize_graphs()
        with pytest.raises(SystemExit):
            LanggraphAgent(graph=None)
    finally:
        os.chdir(cwd)
