from pathlib import Path
import importlib.util
import pytest

pytest.importorskip("yaml")

GRAPH_PATH = Path(__file__).resolve().parents[1] / "src" / "aion" / "server" / "langgraph" / "graph.py"

spec = importlib.util.spec_from_file_location("graph", GRAPH_PATH)
graph = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(graph)
_load_simple_yaml = graph._load_simple_yaml


def test_load_simple_yaml(tmp_path: Path) -> None:
    content = (
        "aion:\n"
        "  graph:\n"
        "    example: \"./module.py:graph\"\n"
        "  env: \".env\"\n"
    )
    yaml_file = tmp_path / "aion.yaml"
    yaml_file.write_text(content)

    data = _load_simple_yaml(yaml_file)

    assert data["aion"]["graph"]["example"] == "./module.py:graph"
    assert data["aion"]["env"] == ".env"
