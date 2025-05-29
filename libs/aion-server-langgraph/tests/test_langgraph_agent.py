import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")

from aion.server.langgraph.a2a.agent import LanggraphAgent
from aion.server.langgraph.graph import GRAPHS


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
    config = {"graphs": {"g1": "./mod.py:create"}}
    cfg_path = tmp_path / "langgraph.json"
    cfg_path.write_text(json.dumps(config))
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        agent = LanggraphAgent(graph=GRAPHS["g1"])
        assert agent.graph is GRAPHS["g1"]
    finally:
        os.chdir(cwd)


def test_langgraph_agent_no_graphs(tmp_path: Path) -> None:
    cfg_path = tmp_path / "langgraph.json"
    cfg_path.write_text(json.dumps({"graphs": {}}))
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(SystemExit):
            LanggraphAgent(graph=None)
    finally:
        os.chdir(cwd)
