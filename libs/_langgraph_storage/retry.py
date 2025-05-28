import asyncio
import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


class RetryableException(Exception):
    pass


RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (RetryableException,)
OVERLOADED_EXCEPTIONS: tuple[type[BaseException], ...] = ()


def retry_db(func: Callable[P, T]) -> Callable[P, T]:
    attempts = 3

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        for i in range(attempts):
            if i == attempts - 1:
                return await func(*args, **kwargs)
            try:
                return await func(*args, **kwargs)
            except RETRIABLE_EXCEPTIONS:
                await asyncio.sleep(0.01)

    return wrapper
