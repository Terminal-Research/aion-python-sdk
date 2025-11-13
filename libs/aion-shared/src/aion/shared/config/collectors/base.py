from abc import ABC, abstractmethod


class BaseCollector(ABC):
    """Abstract base class for implementing data collectors."""

    @abstractmethod
    def collect(self):
        pass
