import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")

import logging

from aion.server.langgraph.graph import GRAPHS, initialize_graphs


def test_initialize_graphs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    module_path = tmp_path / "src" / "path" / "to" / "your" / "module.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        """
class DummyGraph:
    def compile(self):
        return "compiled"

def create_graph():
    return DummyGraph()
"""
    )

    config = {
        "graphs": {
            "example_graph": "./src/path/to/your/module.py:create_graph"
        }
    }
    (tmp_path / "langgraph.json").write_text(json.dumps(config))

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        caplog.set_level(logging.INFO)
        initialize_graphs()
    finally:
        os.chdir(cwd)

    assert GRAPHS["example_graph"] == "compiled"
    messages = [r.getMessage() for r in caplog.records]
    assert any("Importing graph 'example_graph'" in msg for msg in messages)
