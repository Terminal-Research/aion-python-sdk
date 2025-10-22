__all__ = [
    "Singleton",
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
