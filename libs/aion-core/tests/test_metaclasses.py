"""Tests for Singleton and SingletonABCMeta metaclasses."""

import pytest
from abc import abstractmethod
from aion.core.metaclasses import Singleton, SingletonABCMeta


class TestSingleton:
    def test_same_instance_returned(self):
        """Verify that same instance returned."""
        class MyClass(metaclass=Singleton):
            pass

        a = MyClass()
        b = MyClass()
        assert a is b

    def test_constructor_called_once(self):
        """Verify that constructor called once."""
        class Counter(metaclass=Singleton):
            calls = 0
            def __init__(self):
                Counter.calls += 1

        Counter()
        Counter()
        assert Counter.calls == 1

class TestSingletonABCMeta:
    def test_singleton_with_abstract_methods(self):
        """Verify that singleton with abstract methods."""
        class AbstractBase(metaclass=SingletonABCMeta):
            @abstractmethod
            def compute(self) -> int: ...

        class Concrete(AbstractBase):
            def compute(self) -> int:
                return 42

        a = Concrete()
        b = Concrete()
        assert a is b
        assert a.compute() == 42

    def test_abstract_class_cannot_be_instantiated(self):
        """Verify that abstract class cannot be instantiated."""
        class AbstractService(metaclass=SingletonABCMeta):
            @abstractmethod
            def run(self): ...

        with pytest.raises(TypeError):
            AbstractService()
