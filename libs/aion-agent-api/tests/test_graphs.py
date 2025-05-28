import json
import os
from pathlib import Path

import pytest

import logging

from aion_agent_api.graph import GRAPHS, initialize_graphs


def test_initialize_graphs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    graph_file = tmp_path / "my_graph.py"
    graph_file.write_text(
        """
class DummyGraph:
    def compile(self):
        return "compiled"

def create_graph():
    return DummyGraph()
"""
    )
    config = {"graphs": {"dummy": f"{graph_file}:create_graph"}}
    (tmp_path / "langgraph.json").write_text(json.dumps(config))

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        caplog.set_level(logging.INFO)
        initialize_graphs()
    finally:
        os.chdir(cwd)

    assert GRAPHS["dummy"] == "compiled"
    messages = [r.getMessage() for r in caplog.records]
    assert any("Importing graph 'dummy'" in msg for msg in messages)
