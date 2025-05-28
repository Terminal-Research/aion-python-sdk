import asyncio


# TODO: Make this class thread-safe
class TTLDict:
    def __init__(self, timeout=10):
        self._store = {}
        self._timeouts = {}
        self._tasks = {}  # Track tasks for key expiry
        self._timeout = timeout

    async def _remove_key_after_timeout(self, key):
        await asyncio.sleep(self._timeouts[key])
        if key in self._store:
            del self._store[key]
            del self._timeouts[key]

    def set(self, key, value, timeout=None):
        self._store[key] = value
        key_timeout = timeout if timeout is not None else self._timeout
        self._timeouts[key] = key_timeout

        # Cancel the previous task if it exists
        if key in self._tasks:
            self._tasks[key].cancel()

        # Schedule the removal of the key
        self._tasks[key] = asyncio.create_task(self._remove_key_after_timeout(key))

    def get(self, key, default=None):
        return self._store.get(key, default)

    def __contains__(self, key):
        return key in self._store

    def remove(self, key):
        if key in self._store:
            del self._store[key]
            del self._timeouts[key]
            if key in self._tasks:
                self._tasks[key].cancel()
                del self._tasks[key]

    def keys(self):
        return self._store.keys()

    def values(self):
        return self._store.values()

    def items(self):
        return self._store.items()

    def __repr__(self):
        return f"{self.__class__.__name__}({self._store})"
