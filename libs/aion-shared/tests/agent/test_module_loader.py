"""Tests for ModuleLoader.

Focus areas:
  _is_file_path:
    - .py extension → True
    - "/" in path → True
    - "\\" in path → True
    - starts with "." → True
    - plain dotted path → False

  load():
    - delegates to _load_from_file for file paths
    - delegates to _load_from_dotted_path for dotted paths

  _load_from_file():
    - loads an existing .py file and returns a module
    - raises FileNotFoundError for missing file
    - raises ValueError when spec is None (non-Python file)
    - raises ValueError when exec_module raises

  _load_from_dotted_path():
    - loads a valid installed module (e.g. "os")
    - raises ImportError for unknown module

  load_from_config_path():
    - "module.path" → (module, None)
    - "module.path:ItemName" → (module, "ItemName")
    - "path/file.py:MyClass" → (module, "MyClass")

  discover_object():
    - item_name provided: returns named attribute
    - item_name provided: raises ValueError if missing
    - auto-discover class (subclass of supported type)
    - auto-discover instance (instance of supported type)
    - instance takes priority over class in same module
    - raises ValueError when nothing matches
    - skips classes from other modules
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aion.shared.agent.aion_agent.module_loader import ModuleLoader


class TestIsFilePath:
    def test_py_extension_is_file_path(self):
        """Paths ending in .py are identified as file paths."""
        assert ModuleLoader._is_file_path("my_module.py")

    def test_forward_slash_is_file_path(self):
        """Paths containing a forward slash are identified as file paths."""
        assert ModuleLoader._is_file_path("agents/my_agent")

    def test_backslash_is_file_path(self):
        """Paths containing a backslash are identified as file paths."""
        assert ModuleLoader._is_file_path("agents\\my_agent")

    def test_dot_prefix_is_file_path(self):
        """Paths starting with './' are identified as file paths."""
        assert ModuleLoader._is_file_path("./relative_path")

    def test_dot_dotted_prefix_is_file_path(self):
        """Paths starting with '../' are identified as file paths."""
        assert ModuleLoader._is_file_path("../parent/module.py")

    def test_dotted_import_not_file_path(self):
        """Plain dotted module paths are not identified as file paths."""
        assert not ModuleLoader._is_file_path("my.module.path")

    def test_single_word_not_file_path(self):
        """A single-word module name is not identified as a file path."""
        assert not ModuleLoader._is_file_path("mymodule")


class TestLoadRouting:
    def test_load_calls_file_loader_for_py_path(self, tmp_path):
        """load routes .py file paths to the file loader and returns the module."""
        py_file = tmp_path / "simple.py"
        py_file.write_text("x = 1\n")
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        module = loader.load(str(py_file))
        assert hasattr(module, "x")
        assert module.x == 1

    def test_load_calls_dotted_loader_for_module_path(self):
        """load routes dotted module names to the dotted-path loader and returns the module."""
        loader = ModuleLoader(logger=MagicMock())
        module = loader.load("os.path")
        assert module.__name__ == "posixpath" or "path" in module.__name__

    def test_load_raises_import_error_for_unknown_dotted(self):
        """load raises ImportError for dotted paths that do not resolve to an installed module."""
        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ImportError):
            loader.load("definitely.does.not.exist.xyz")


class TestLoadFromFile:
    def test_loads_valid_py_file(self, tmp_path):
        """_load_from_file loads attributes from a valid Python file."""
        py_file = tmp_path / "agent.py"
        py_file.write_text("AGENT_NAME = 'TestAgent'\nclass MyAgent: pass\n")
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        module = loader._load_from_file(str(py_file))
        assert hasattr(module, "AGENT_NAME")
        assert module.AGENT_NAME == "TestAgent"

    def test_raises_file_not_found_for_missing_file(self, tmp_path):
        """_load_from_file raises FileNotFoundError for a path that does not exist."""
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        with pytest.raises(FileNotFoundError):
            loader._load_from_file(str(tmp_path / "missing.py"))

    def test_relative_path_resolved_against_base_path(self, tmp_path):
        """_load_from_file resolves relative paths against the configured base_path."""
        py_file = tmp_path / "relative.py"
        py_file.write_text("VALUE = 42\n")
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        module = loader._load_from_file("relative.py")
        assert module.VALUE == 42

    def test_raises_value_error_when_exec_raises(self, tmp_path):
        """_load_from_file raises ValueError with 'Failed to execute module' when the file raises on import."""
        py_file = tmp_path / "broken.py"
        py_file.write_text("raise RuntimeError('broken module')\n")
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        with pytest.raises(ValueError, match="Failed to execute module"):
            loader._load_from_file(str(py_file))

    def test_absolute_path_used_directly(self, tmp_path):
        """_load_from_file uses an absolute path directly, ignoring base_path."""
        py_file = tmp_path / "absolute.py"
        py_file.write_text("ABSOLUTE = True\n")
        # Use a different base_path to ensure absolute path ignores it
        loader = ModuleLoader(base_path=Path("/tmp"), logger=MagicMock())
        module = loader._load_from_file(str(py_file))
        assert module.ABSOLUTE is True


class TestLoadFromDottedPath:
    def test_loads_stdlib_module(self):
        """_load_from_dotted_path loads and returns a known stdlib module."""
        loader = ModuleLoader(logger=MagicMock())
        module = loader._load_from_dotted_path("json")
        import json
        assert module is json

    def test_loads_nested_stdlib_module(self):
        """_load_from_dotted_path loads nested stdlib modules like os.path."""
        loader = ModuleLoader(logger=MagicMock())
        module = loader._load_from_dotted_path("os.path")
        import os.path
        assert module is os.path

    def test_raises_import_error_for_unknown(self):
        """_load_from_dotted_path raises ImportError with 'Could not import module' for unknown paths."""
        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ImportError, match="Could not import module"):
            loader._load_from_dotted_path("definitely.not.a.module.xyz")


class TestLoadFromConfigPath:
    def test_module_only_returns_none_item(self):
        """load_from_config_path with no colon returns (module, None)."""
        loader = ModuleLoader(logger=MagicMock())
        module, item_name = loader.load_from_config_path("json")
        import json
        assert module is json
        assert item_name is None

    def test_module_with_item_name(self):
        """load_from_config_path with 'module:Item' returns the item name string."""
        loader = ModuleLoader(logger=MagicMock())
        module, item_name = loader.load_from_config_path("json:JSONDecodeError")
        assert item_name == "JSONDecodeError"

    def test_file_path_with_item_name(self, tmp_path):
        """load_from_config_path with 'file.py:MyClass' returns the class name and module."""
        py_file = tmp_path / "myagent.py"
        py_file.write_text("class MyAgent: pass\n")
        loader = ModuleLoader(base_path=tmp_path, logger=MagicMock())
        module, item_name = loader.load_from_config_path(f"{py_file}:MyAgent")
        assert item_name == "MyAgent"
        assert hasattr(module, "MyAgent")

    def test_empty_item_part_returns_none(self):
        """load_from_config_path returns None for item_name when no colon is present."""
        loader = ModuleLoader(logger=MagicMock())
        _, item_name = loader.load_from_config_path("os")
        assert item_name is None


def _make_module(name: str, attrs: dict) -> types.ModuleType:
    """Build an in-memory module with the given attributes."""
    mod = types.ModuleType(name)
    mod.__name__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
        # For classes, fix __module__ so discover_object doesn't skip them
        if isinstance(v, type):
            v.__module__ = name
    return mod


class BaseType:
    pass


class SubType(BaseType):
    pass


class OtherType:
    pass


class TestDiscoverObject:
    def test_explicit_item_name_found(self):
        """discover_object returns the named attribute when item_name is provided and found."""
        mod = _make_module("test_mod", {"MyClass": SubType})
        loader = ModuleLoader(logger=MagicMock())
        result = loader.discover_object(mod, [BaseType], item_name="MyClass")
        assert result is SubType

    def test_explicit_item_name_not_found_raises(self):
        """discover_object raises ValueError with 'not found in module' when item_name is absent."""
        mod = _make_module("test_mod", {})
        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ValueError, match="not found in module"):
            loader.discover_object(mod, [BaseType], item_name="Missing")

    def test_auto_discover_subclass(self):
        """discover_object auto-discovers a class that subclasses a supported type."""
        class LocalBase:
            pass

        class LocalSub(LocalBase):
            pass

        mod = _make_module("test_mod_sub", {"LocalSub": LocalSub})
        loader = ModuleLoader(logger=MagicMock())
        result = loader.discover_object(mod, [LocalBase])
        assert result is LocalSub

    def test_auto_discover_instance(self):
        """discover_object auto-discovers an instance of a supported type."""
        class LocalBase:
            pass

        instance = LocalBase()
        mod = _make_module("test_mod_inst", {"my_instance": instance})
        loader = ModuleLoader(logger=MagicMock())
        result = loader.discover_object(mod, [LocalBase])
        assert result is instance

    def test_instance_priority_over_class(self):
        """discover_object returns an instance over a class when both are present in the module."""
        class LocalBase:
            pass

        class LocalSub(LocalBase):
            pass

        instance = LocalBase()

        mod = _make_module("test_mod_prio", {"LocalSub": LocalSub, "my_inst": instance})
        loader = ModuleLoader(logger=MagicMock())
        result = loader.discover_object(mod, [LocalBase])
        # Instance should win over class
        assert result is instance

    def test_no_match_raises_value_error(self):
        """discover_object raises ValueError with 'No supported object found' when nothing matches."""
        mod = _make_module("test_mod_empty", {"x": 42, "y": "hello"})
        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ValueError, match="No supported object found"):
            loader.discover_object(mod, [BaseType])

    def test_skips_private_members_in_auto_discovery(self):
        """discover_object skips private (underscore-prefixed) members during auto-discovery."""
        class LocalBase:
            pass

        instance = LocalBase()
        mod = _make_module("test_mod_priv", {"_private": instance, "public_x": 42})
        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ValueError):
            loader.discover_object(mod, [LocalBase])

    def test_skips_classes_from_other_modules(self):
        """Classes imported from other modules should not be auto-discovered."""
        # SubType.__module__ is set to this test file, not to our fake module name
        mod = types.ModuleType("other_mod")
        mod.__name__ = "other_mod"
        # Don't fix __module__ on SubType — it belongs to a different module
        mod.SubType = SubType

        loader = ModuleLoader(logger=MagicMock())
        with pytest.raises(ValueError):
            loader.discover_object(mod, [BaseType])
