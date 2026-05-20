from abc import ABCMeta

__all__ = [
    "Singleton",
    "SingletonABCMeta",
]


class Singleton(type):
    """Metaclass that creates singleton classes.

    This metaclass ensures that only one instance of a class can exist.
    When a class uses this metaclass, all attempts to create new instances
    will return the same object.

    Attributes:
        _instances (dict): Dictionary storing singleton instances keyed by class.
    """
    _instances = {}

    def __call__(cls, *args, **kwargs):
        """Create or return existing instance of the class."""
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class SingletonABCMeta(ABCMeta):
    """Combined metaclass that provides both Singleton and ABC functionality.

    This metaclass combines the singleton pattern with abstract base class
    functionality, allowing classes to be both singletons and define abstract
    methods/properties.

    Use this when you need a singleton class that also inherits from ABC or
    implements abstract methods.

    Attributes:
        _instances (dict): Dictionary storing singleton instances keyed by class.
    """
    _instances = {}

    def __call__(cls, *args, **kwargs):
        """Create or return existing instance of the class."""
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
