from datetime import datetime
from typing import Dict, Any, Optional, List, Union

import aiohttp


class AionLogstashClient:
    """
    Asynchronous Logstash client for publishing logs via HTTP.
    Supports single and batch log sending through unified interface.
    """

    def __init__(self, url: str, timeout: int = 5):
        """
        Initialize the Logstash client.

        Args:
            url: Logstash HTTP input URL (e.g., 'http://localhost:8080')
            timeout: Request timeout in seconds
        """
        self.url = url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_closed = False
        self.__logger = None

    @property
    def _logger(self):
        """Lazy logger initialization to avoid circular imports"""
        if self.__logger is None:
            from aion.shared.logging.factory import get_logger
            self.__logger = get_logger("AionLogstashClient", use_aion_api=False)
        return self.__logger


    def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create cached session.

        Returns:
            Active ClientSession instance
        """
        if self._session is None or self._session.closed or self._session_closed:
            self._session = aiohttp.ClientSession()
            self._session_closed = False
        return self._session


    async def close_session(self):
        """
        Close the cached session.
        Should be called when the client is no longer needed.
        """
        if self._session and not self._session.closed:
            await self._session.close()
            self._session_closed = True


    async def send(self, data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> bool:
        """
        Send log(s) to Logstash via HTTP.

        Args:
            data: Single log dictionary or list of log dictionaries

        Returns:
            True if sent successfully, False otherwise

        Examples:
            # Send single log
            await client.send({"message": "test", "level": "INFO"})

            # Send batch
            await client.send([
                {"message": "log1", "level": "INFO"},
                {"message": "log2", "level": "ERROR"}
            ])
        """
        # Normalize to list
        logs = [data] if isinstance(data, dict) else data

        if not logs:
            return True

        # Add timestamps to logs that don't have them
        for log in logs:
            if "timestamp" not in log:
                log["timestamp"] = datetime.utcnow().isoformat()

        # Prepare payload (single dict or array of dicts)
        payload = logs[0] if len(logs) == 1 else logs

        try:
            session = self._get_session()

            async with session.post(
                    self.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                response.raise_for_status()
                self._logger.debug(f"Successfully sent {len(logs)} log entries")
                return True

        except Exception as ex:
            self._logger.error(f"Failed to send log(s) to Logstash: {ex}")
            return False
