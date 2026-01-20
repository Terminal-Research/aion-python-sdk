"""Dynamic FastAPI application mounting utilities."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Dict

from aion.shared.logging import get_logger
from fastapi import FastAPI
from pydantic import BaseModel

logger = get_logger()


class HttpConfig(BaseModel):
    """Configuration model for HTTP application mounts."""

    apps: Dict[str, str] = {}


class DynamicAppLoader:
    """Load ASGI applications from module paths."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def load(self, import_str: str) -> Any:
        """Load an application from ``module:variable`` string."""
        if import_str in self._cache:
            return self._cache[import_str]

        module_part, _, attr = import_str.partition(":")
        if not attr:
            raise ValueError(f"Invalid import string: '{import_str}'")

        if module_part.endswith(".py") or "/" in module_part or module_part.startswith("."):
            path = Path(module_part).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Module file not found: {path}")
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module from {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[arg-type]
        else:
            module = importlib.import_module(module_part)

        if not hasattr(module, attr):
            raise AttributeError(f"{attr} not found in module {module_part}")

        app = getattr(module, attr)
        if not callable(app):
            raise TypeError(f"Loaded object '{attr}' from '{module_part}' is not callable")

        self._cache[import_str] = app
        return app


class MountManager:
    """Manage mounting of applications onto a main ``FastAPI`` app."""

    def __init__(self, main_app: FastAPI, loader: DynamicAppLoader | None = None) -> None:
        self.main_app = main_app
        self.loader = loader or DynamicAppLoader()
        self.mounted: Dict[str, Any] = {}

    def mount_apps(self, config: Dict[str, str]) -> None:
        """Mount applications from the provided configuration."""
        for mount_path, import_str in config.items():
            try:
                sub_app = self.loader.load(import_str)
            except Exception as exc:  # pragma: no cover - logging only
                logger.error("Failed to load app %s: %s", import_str, exc)
                continue
            logger.info("Mounting %s at %s", import_str, mount_path)
            self.main_app.mount(mount_path, sub_app)
            self.mounted[mount_path] = sub_app


class DynamicMounter:
    """Orchestrate loading of configuration and mounting apps."""

    def __init__(self, main_app: FastAPI, config_path: str | Path = "aion.yaml") -> None:
        self.main_app = main_app
        self.config_path = Path(config_path)
        self.mount_manager = MountManager(main_app)

    def load_from_config(self) -> None:
        """Load configuration from ``aion.yaml`` and mount applications."""
        if not self.config_path.exists():
            logger.warning("Configuration file %s not found", self.config_path)
            return

        import yaml

        with self.config_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        http_data = raw.get("aion", {}).get("http", {}) if isinstance(raw, dict) else {}
        http_cfg = HttpConfig(apps=http_data)
        self.mount_manager.mount_apps(http_cfg.apps)
