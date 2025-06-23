from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("starlette")
from starlette.applications import Starlette

from aion.server.langgraph.webapp import DynamicMounter


def test_dynamic_mounter(tmp_path: Path) -> None:
    app_file = tmp_path / "webapp.py"
    app_file.write_text(
        "from starlette.applications import Starlette\napp = Starlette()\n"
    )

    config = f"aion:\n  http:\n    /sub: '{app_file}:app'\n"
    (tmp_path / "aion.yaml").write_text(config)

    main_app = Starlette()
    mounter = DynamicMounter(main_app, config_path=tmp_path / "aion.yaml")
    mounter.load_from_config()

    paths = [route.path for route in main_app.routes]
    assert "/sub" in paths

