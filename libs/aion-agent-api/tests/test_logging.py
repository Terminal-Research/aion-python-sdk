import structlog
import aion_agent_api.logging as logconf


def test_configures_structlog() -> None:
    logger = structlog.get_logger(__name__)
    assert isinstance(logger, structlog.stdlib.BoundLogger)
