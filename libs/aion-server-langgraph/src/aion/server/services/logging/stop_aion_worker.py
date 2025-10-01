from aion.server.core.base import BaseExecuteService


# todo update to use DI pattern


class LoggingStopAionWorkerService(BaseExecuteService):

    async def execute(self):
        from aion.shared.logging.handlers.aion_api import AionApiLogManager

        aion_logging_manager = AionApiLogManager()
        await aion_logging_manager.shutdown()
