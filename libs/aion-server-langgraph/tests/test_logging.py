import logging
import structlog
import aion.server.langgraph.logging as logconf


def test_configures_structlog() -> None:
    logger = structlog.get_logger(__name__)
    assert isinstance(logger, structlog.stdlib.BoundLogger)


def test_root_logger_has_handler() -> None:
    root_logger = logging.getLogger()
    assert root_logger.handlers, "Root logger should have a console handler"
