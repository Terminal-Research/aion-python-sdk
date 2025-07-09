import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("a2a")

import logging

from aion.server.langgraph.graph import graph_manager


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

    config = (
        "aion:\n"
        "  graph:\n"
        "    example_graph: \"./src/path/to/your/module.py:create_graph\"\n"
    )
    (tmp_path / "aion.yaml").write_text(config)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        caplog.set_level(logging.INFO)
        graph_manager.initialize_graphs()
    finally:
        os.chdir(cwd)

    assert graph_manager.get_graph("example_graph") == "compiled"
    messages = [r.getMessage() for r in caplog.records]
    assert any("Importing graph 'example_graph'" in msg for msg in messages)
