from aion.shared.logging import get_logger
from aion.shared.settings import api_settings, platform_settings

from aion.server.core.base import BaseExecuteService

logger = get_logger()


# todo update to use DI pattern


class LoggingStartAionWorkerService(BaseExecuteService):

    async def execute(self):
        from aion.shared.logging.handlers.aion_api import AionApiLogManager

        aion_logging_manager = AionApiLogManager()
        if not platform_settings.logstash_endpoint:
            logger.warning("No logstash endpoint configured. Skipped aion logging.")
            aion_logging_manager.disable()
            return

        if not api_settings.client_id:
            logger.warning("No client_id configured. Skipped aion logging.")
            aion_logging_manager.disable()
            return

        await aion_logging_manager.start(
            url=platform_settings.logstash_endpoint,
            client_id=api_settings.client_id)
        logger.info("Started Aion API logging worker")
