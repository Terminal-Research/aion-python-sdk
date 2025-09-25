from aion.shared.logging import get_logger


def replace_uvicorn_loggers():
    """Replace uvicorn's default loggers with your custom one"""

    # Replace uvicorn's access logger
    for logger_name in ("uvicorn.access", "uvicorn", "starlette", "fastapi"):
        get_logger(logger_name, use_stream=True, use_http=None)
