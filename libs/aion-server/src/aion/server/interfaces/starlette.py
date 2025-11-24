from abc import ABC

from a2a.server.request_handlers import RequestHandler

__all__ = [
    "IRequestHandler",
]


class IRequestHandler(RequestHandler, ABC):
    pass
