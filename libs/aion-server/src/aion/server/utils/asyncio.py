import asyncio


def has_event_loop() -> bool:
    """Check if event loop exists"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
